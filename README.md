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

> **A note on iterating:** for pure UI/backend work, plain `reflex run` (browser, hot
> reload) is still the fastest loop — the app code is identical. The moment you use a
> native feature (`desktop.notify`, window controls, dialogs, `invoke`), switch to
> `reflex-desktop dev`: the same hot-reloading dev server, but inside the real Tauri
> webview where `window.__TAURI__` exists.

There's a fuller, multi-page example (background events, forms, native window controls) in
[`example/`](example/) if you want something less trivial to poke at.

---

## Commands

```bash
reflex-desktop dev                # hot reload inside the real desktop webview
reflex-desktop run                # build the app, then launch it
reflex-desktop run --skip-build   # relaunch an already-built app, no recompile
reflex-desktop build              # build only (release)
reflex-desktop build --bundle     # also produce installers (.dmg/.msi/.AppImage/.deb)
reflex-desktop doctor             # check your machine has what it needs to build
```

`dev` starts the normal `reflex run` dev server, then opens a Tauri shell pointed at it —
edit your Python and the window hot-reloads, while native bridge features
(`window.__TAURI__`) keep working. The dev shell lives in `<app>/tauri-dev/` and only
compiles Rust the first time (it needs the Tauri CLI, like `--bundle`).

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
| **`--bundle` / `dev`** | the Tauri CLI: `cargo install tauri-cli --locked` (prebuilt: `cargo binstall tauri-cli`) |
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
    tray=True,                       # system tray icon with a Show / Quit menu
    with_global_tauri=True,          # default — exposes window.__TAURI__ for the bridge below
    tauri_plugins=("notification", "dialog", "clipboard-manager"),
    extra_capabilities=("os:default",),
)
```

**Ports.** In `embedded` mode the backend binds a loopback port baked into the build. The
default is derived from your app's `identifier`, so two installed reflex-desktop apps
don't collide; pass `port=...` to pin one explicitly. If something else already owns the
port at launch, the app explains that in a native error dialog (a second launch of the
*same* app just focuses the running window).

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

# Native file dialogs (needs tauri_plugins=("dialog",)) — the picked path arrives in
# a normal event handler, and since the backend runs locally you can open() it directly:
rx.button("open…", on_click=desktop.open_file(State.handle_path, filters={"CSV": ["csv"]}))
rx.button("save as…", on_click=desktop.save_file(State.handle_save, default_path="report.csv"))

# Clipboard (needs tauri_plugins=("clipboard-manager",)):
rx.button("copy", on_click=desktop.clipboard_write("hello"))
rx.button("paste", on_click=desktop.clipboard_read(State.handle_clipboard))

# Any custom #[tauri::command] you added to main.rs:
rx.button("go", on_click=desktop.invoke("my_command", {"file_path": "/tmp/x"}))
```

`desktop.invoke()` converts argument names to the camelCase spellings Tauri expects on
the wire (`file_path` → `filePath`), which is the classic silent failure when calling
commands by hand; pass `convert_keys=False` if your command is declared with
`#[tauri::command(rename_all = "snake_case")]`.

**Auto-updates & signing.** `tauri_plugins=("updater",)` plus
`updater_endpoints=(...)`/`updater_pubkey=...` wires Tauri's updater into the shell and
turns on signed update artifacts at bundle time — see [docs/updater.md](docs/updater.md).
OS code signing / macOS notarization is env-var driven at `--bundle` time — see
[docs/signing.md](docs/signing.md).

`desktop.notify()` asks for OS notification permission before sending (otherwise it's a
silent no-op on macOS/Windows). On Linux, a *bundled* app (`--bundle`, which installs a
`.desktop` entry) shows banners reliably; a bare dev binary may route them to the
notification tray instead.

Anything the plugin doesn't cover, you can still do by hand — `src-tauri/` is a standard
Tauri project, so add crates, edit `main.rs`, whatever you need.

---

## Limitations

- **`reflex-desktop run`/`build` package a production build** — the window loads a
  prebuilt static frontend, so editing code doesn't live-update it. That's what
  `reflex-desktop dev` is for: the hot-reloading dev server inside the real webview.
- **`dev` uses the dev server as the backend**, so the embedded in-process interpreter
  isn't exercised until you do a real `run`/`build`. Smoke-test the built app before
  shipping.
- **The dev shell compiles Rust once** (first `dev` invocation) and whenever you change
  native config (tray, plugins). Pure Python edits never recompile.

### What's solid vs. still in progress

- **`remote`** — done and verified end-to-end.
- **`embedded`** — self-contained builds and installers: the binary boots a bundled
  interpreter in-process and serves your Reflex backend locally. Installed bundles find
  the bundled interpreter through relative library paths (`$ORIGIN` on Linux,
  `@rpath`/`@executable_path` on macOS, exe-adjacent DLLs on Windows), the backend
  dependencies are pinned to the exact reflex version you built with, and unused runtime
  pieces (stdlib test suite, Tcl/Tk, bytecode caches) are trimmed from the bundle. CI
  installs the built `.deb` and boots the embedded backend from the installed location on
  every push.
- **macOS/Windows** builds compile in CI; broader real-hardware validation (and signing —
  see [docs/signing.md](docs/signing.md)) is on you for now.

> **Heads up for snap users:** if you build from a snap-confined terminal (e.g. VS Code
> installed via snap), the launched binary would otherwise inherit the snap's GTK/locale
> paths and crash. `reflex-desktop run` detects this and restores the originals before
> launch, so it just works.
