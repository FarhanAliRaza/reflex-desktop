# reflex-desktop

Ship your [Reflex](https://reflex.dev) app as a real desktop app — a native window,
an icon in the dock, an installer you can hand to someone — with
[Tauri](https://tauri.app) doing the heavy lifting.

You write a normal Reflex app. `reflex-desktop` wraps it in a lightweight native shell
(the OS's built-in WebView, not a bundled Chromium) and either points it at a backend you
host, or tucks the whole Python backend *inside* the binary so the finished app needs
nothing installed on the machine it runs on.

```bash
reflex-desktop run     # build the desktop app and open it
```

---

## How it works

A Reflex app is two halves: a static frontend (a React-Router single-page app) and a
Python backend. `reflex-desktop` builds the frontend with Reflex's own toolchain, drops it
into a Tauri shell, and connects it to the backend one of two ways:

- **`embedded`** *(default)* — the Python backend runs *in-process* inside the Tauri binary
  via [PyO3](https://pyo3.rs), against a bundled standalone interpreter. The result is
  self-contained: no Python, no separate server, nothing to install. Just launch the app.
- **`remote`** — a frontend-only desktop app that talks to a backend you run somewhere else
  over a URL. Good when the backend already lives on a server.

Most people want `embedded`. Reach for `remote` when the backend is hosted separately.

---

## Your first app

Never built a Reflex app? Start with the framework, not with us — go through the
[Reflex quickstart](https://reflex.dev/docs/getting-started/introduction/) first. Everything
there is exactly the same here; `reflex-desktop` only changes the *last* step, where the app
gets packaged.

If you just want to see it work, here's a hello-world from scratch.

**1. Create a Reflex app** (see the [installation guide](https://reflex.dev/docs/getting-started/installation/)):

```bash
mkdir hello && cd hello
uv venv && source .venv/bin/activate
uv pip install reflex
reflex init                 # pick a blank template
```

**2. Add `reflex-desktop`.** It isn't on PyPI yet, so install it from this repo:

```bash
uv pip install -e /path/to/reflex-desktop
```

**3. Write the app.** Replace `hello/hello.py` with a tiny counter — the count lives in
Python state, so every click round-trips to the backend and back:

```python
import reflex as rx


class State(rx.State):
    count: int = 0

    @rx.event
    def increment(self):
        self.count += 1


def index():
    return rx.center(
        rx.vstack(
            rx.heading(f"Count: {State.count}"),
            rx.button("Click me", on_click=State.increment),
            align="center",
        ),
        height="100vh",
    )


app = rx.App()
app.add_page(index)
```

**4. Turn on the desktop plugin** in `rxconfig.py`:

```python
import reflex as rx

from reflex_desktop import DesktopPlugin

config = rx.Config(
    app_name="hello",
    cors_allowed_origins=["*"],          # let the Tauri webview reach the backend
    plugins=[
        DesktopPlugin(backend="embedded"),
    ],
)
```

**5. Build and launch:**

```bash
reflex-desktop run
```

That's it — a native window with your app inside. The first build pulls down a standalone
Python interpreter and compiles the Rust shell, so it takes a few minutes; everything after
that is quick.

> **A note on iterating:** for day-to-day development, use plain `reflex run` — the normal
> Reflex dev server with hot reload, in your browser. The app code is identical. Only switch
> to `reflex-desktop` when you're ready to package and smoke-test the desktop build. See
> [Limitations](#limitations) for why.

There's a fuller, multi-page example (background events, forms, native window controls) in
[`example/`](example/) if you want something less trivial to poke at.

---

## Commands

```bash
reflex-desktop run                # build the app, then launch it
reflex-desktop run --skip-build   # relaunch an already-built app, no recompile
reflex-desktop build              # build only (release)
reflex-desktop build --bundle     # also produce installers (.dmg/.msi/.AppImage/.deb)
reflex-desktop codegen            # generate typed Python bindings for your Tauri commands
reflex-desktop doctor             # check your machine has what it needs to build
```

`run` does a fast **debug** build by default (like `cargo run`); add `--release` for an
optimized one. `build` defaults to **release**. The generated Tauri project lives under
`<app>/tauri/`, and it's a perfectly normal Tauri project — edit `src-tauri/` directly if
you ever need to.

---

## Prerequisites

Two very different audiences here:

**People who *use* the shipped app** need nothing. The installer is self-contained — in
`embedded` mode it even carries its own Python. (The one asterisk: Windows needs the
WebView2 runtime, which already ships with Windows 11 and recent Windows 10, and the bundler
can include the installer for it.)

**People who *build* the app** need a native toolchain, because the build compiles a Rust
shell from source — a `pip install` can't ship you a compiler. This is the standard
[Tauri setup](https://tauri.app/start/prerequisites/):

| | What you need |
|---|---|
| **All platforms** | Rust, via [rustup](https://rustup.rs) |
| **Linux** | `libwebkit2gtk-4.1-dev`, `build-essential`, `libssl-dev`, `librsvg2-dev`, `libayatana-appindicator3-dev`, plus `curl`/`wget`/`file` |
| **macOS** | Xcode Command Line Tools (`xcode-select --install`) |
| **Windows** | Microsoft C++ Build Tools (MSVC) + the WebView2 runtime |
| **`--bundle`** | the Tauri CLI: `cargo install tauri-cli --locked` |
| **`embedded`** | a network connection on the *first* build (it downloads an interpreter and installs the backend) |

Package names drift between distros — Tauri's
[prerequisites page](https://tauri.app/start/prerequisites/) is the source of truth.

Not sure you're set up? Run:

```bash
reflex-desktop doctor            # Rust + platform WebView deps
reflex-desktop doctor --bundle   # also checks the Tauri CLI, for installers
```

`build` and `run` run these checks first anyway, and stop with copy-pasteable install
commands if something's missing — no cryptic linker errors halfway through a compile.

---

## Customizing the window, icon & native APIs

`DesktopPlugin` is the single source of truth for the generated `tauri.conf.json`,
capabilities, and plugin dependencies. It re-applies them on every build, and leaves your
hand edits outside the managed regions alone. Configure it in `rxconfig.py`:

```python
DesktopPlugin(
    backend="embedded",
    window_width=1100, window_height=750,
    resizable=True, min_width=900, min_height=650, center=True,
    theme="Dark",                    # + fullscreen / decorations / transparent / always_on_top
    icon="assets/logo.png",          # a PNG, copied over the bundle icons
    with_global_tauri=True,          # default — exposes window.__TAURI__ for the bridge below
    tauri_plugins=("notification",), # pulls in tauri-plugin-<name>, wired up + permissioned
    extra_capabilities=("os:default",),
)
```

**Icons.** `icon=` copies one PNG over the four default bundle icons. For the full platform
set (`.ico`/`.icns` and every size), run
`cd <app>/tauri/src-tauri && cargo tauri icon <path>`.

**Driving the OS from Python.** `reflex_desktop.desktop` wraps `window.__TAURI__` so a Reflex
event handler can control the window or fire native APIs (needs `with_global_tauri=True`,
which is the default):

```python
from reflex_desktop import desktop

rx.button("minimize", on_click=desktop.minimize())
rx.button("max/restore", on_click=desktop.toggle_maximize())
rx.button("close", on_click=desktop.close())
rx.button("notify", on_click=desktop.notify("Title", "Body"))  # needs tauri_plugins=("notification",)
```

`desktop.notify()` asks for OS notification permission before sending (otherwise it's a
silent no-op on macOS/Windows). On Linux, a *bundled* app (`--bundle`, which installs a
`.desktop` entry) shows banners reliably; a bare dev binary may route them to the
notification tray instead.

**Calling native code: `desktop.invoke()`.** The window helpers above are convenience
wrappers; `desktop.invoke()` is the general escape hatch to any Tauri command, so you don't
have to hand-write `rx.call_script("window.__TAURI__.core.invoke(...)")`. Pass arguments as a
dict and, if you want the result back, a `callback` — the returned promise is resolved before
your handler runs:

```python
from reflex_desktop import desktop

# a Tauri plugin command — enable the plugin first: tauri_plugins=("fs",)
rx.button(
    "read file",
    on_click=desktop.invoke(
        "plugin:fs|read_text_file",
        {"path": "/etc/hostname"},
        callback=State.on_file_read,   # State.on_file_read(self, contents)
    ),
)

# your own #[tauri::command]
rx.button("do native thing", on_click=desktop.invoke("my_command", {"x": 1}))
```

> **Note on the `embedded` backend:** because the Python backend runs *in-process on the
> user's machine*, you usually don't need the bridge at all for system access — just use
> plain Python in an event handler (`open()`, `pathlib`, `subprocess`, …), running with the
> user's own privileges. Reach for `desktop.invoke()` when you specifically want the
> *webview/Tauri* side (native dialogs, OS integration, a Rust command). In `remote` mode,
> where Python runs on a server, the bridge is the only way to touch the local machine.
> For slow filesystem/subprocess work in an event handler, use a Reflex background event
> (`@rx.event(background=True)`) so you don't block the backend's event loop.

### Adding your own Rust command (and calling it type-safely)

`src-tauri/` is a standard Tauri project, so you add native code the normal Tauri way — just
write the command. **You don't register it by hand:** on every build, reflex-desktop scans
`src-tauri/src` for `#[tauri::command]` functions and wires each one into
`generate_handler!` (and grants it the capability to be invoked). Add the function, and it's
live on the next build — rebuild-safe, no list to maintain.

```rust
// src-tauri/src/main.rs — add anywhere; the build registers it for you.
#[tauri::command(rename_all = "snake_case")]
fn read_note(file_path: String) -> Result<String, String> {
    std::fs::read_to_string(&file_path).map_err(|e| e.to_string())
}
```

Then generate a **typed Python binding** from that Rust signature so the call site is
checked and autocompleted instead of stringly-typed:

```bash
reflex-desktop codegen      # writes <app_pkg>/desktop_commands.py
```

```python
from my_app import desktop_commands as native

# read_note(file_path: str) -> str  — the signature is generated from the Rust source
rx.button("open", on_click=native.read_note(file_path="/notes/today.md", callback=State.on_note))
```

The generator maps Rust types to Python (`String`→`str`, `Option<T>`→`T | None`,
`Vec<T>`→`list[T]`, `Result<T, E>`→`T`, …), drops Tauri-injected arguments
(`AppHandle`/`Window`/`State`), and records the return type so you know what your `callback`
receives. Re-run it whenever a signature changes — or set `DesktopPlugin(command_stubs=True)`
to regenerate automatically on every build.

> **One Tauri gotcha:** annotate commands with `#[tauri::command(rename_all = "snake_case")]`
> so the argument names on the wire match the Rust names — that's what the generated bindings
> (and `desktop.invoke`) send.

If you'd rather not add Rust at all, official Tauri plugins via `tauri_plugins=(...)` are also
rebuild-safe — enable one and call it with `desktop.invoke("plugin:<name>|<command>", ...)`.
And anything the plugin doesn't cover you can still do by hand in `src-tauri/`.

---

## Limitations

`reflex-desktop` packages a **production build**. It is not a live dev environment, and on
purpose:

- **No hot reload.** The window loads a *prebuilt static* frontend, and the embedded backend
  starts without recompiling, so editing your app doesn't live-update the running window.
- **Every build is a full build.** There's no incremental loop yet — each `run`/`build`
  re-exports the frontend and compiles the Tauri binary (debug is faster than release, but
  it's still a `cargo` compile).
- **`reflex-desktop dev` isn't implemented.** v1 is build-only; a live desktop dev mode may
  come later.

So: **develop with `reflex run`** (browser, hot reload, fast), and use `reflex-desktop` to
package and test the desktop build when you're ready to ship.

### What's solid vs. still in progress

- **`remote`** — done and verified end-to-end.
- **`embedded`** — works end-to-end as an in-place (dev) build: the binary boots a bundled
  interpreter in-process and serves your Reflex backend locally. Producing a fully
  *relocatable* installer (an installed `.app`/AppImage you can move to another machine)
  still has a few loose ends — a relocation-safe library path for the bundled interpreter,
  macOS signing, and trimming the bundle size.

> **Heads up for snap users:** if you build from a snap-confined terminal (e.g. VS Code
> installed via snap), the launched binary would otherwise inherit the snap's GTK/locale
> paths and crash. `reflex-desktop run` detects this and restores the originals before
> launch, so it just works.
