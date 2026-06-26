# reflex-desktop

Package a [Reflex](https://reflex.dev) app as a native desktop app with
[Tauri](https://tauri.app) (system WebView).

A Reflex app is a static React-Router SPA plus a Python ASGI backend. `reflex-desktop`
builds the frontend with Reflex's native toolchain (no bun/npm inside Tauri), wraps the
prebuilt static frontend in a Tauri shell, and connects it to the backend in one of two
modes:

- **`remote`** — frontend-only desktop app talking to a hosted backend over a URL.
- **`embedded`** — the Python ASGI backend runs in-process inside the Tauri binary via
  PyO3, against a bundled relocatable interpreter (python-build-standalone) and the app's
  site-packages — self-contained, no Python required on the target.

## Status

- **remote** — complete and verified end-to-end (env.json rebasing, scaffold, static
  copy, `cargo build` of the embedded-frontend binary).
- **embedded** — verified end-to-end for an in-place (dev) build: the binary links
  `libpython` (PyO3), boots a bundled python-build-standalone interpreter in-process, and
  serves the Reflex ASGI backend on `127.0.0.1:<port>` — `/_health` returns and the
  socket.io `/_event` WebSocket accepts the Tauri origin. Startup is write-free/backend-only
  (`REFLEX_SKIP_COMPILE` set via the environment API; no `bun install`/compile). Remaining
  for a shippable *bundle* (M2): make the bundled resources resolve via Tauri's
  `resource_dir()` (not the dev `REFLEX_DESKTOP_RESOURCE_DIR`), a `$ORIGIN`-relative libpython
  rpath for the relocated interpreter, macOS signing, and bundle-size trimming.

When the build runs inside a snap-confined terminal (e.g. VS Code installed as a snap), the
launched native binary inherits the snap's GTK/GIO/locale paths and would crash with
`undefined symbol: __libc_pthread_init`. `reflex-desktop run` restores the snap's recorded
originals (`*_VSCODE_SNAP_ORIG`) before launch, so this is handled automatically.

## Prerequisites

There are two audiences with very different requirements:

- **End users who install the shipped app** need **nothing** — the `.dmg`/`.msi`/
  `.AppImage`/`.deb` is self-contained (in `embedded` mode it even bundles the Python
  interpreter). The only caveat is Windows' WebView2 runtime, which ships with Windows 11
  and recent Windows 10 (and the Tauri bundler can include the bootstrapper).
- **Developers who *build* the app** (run `reflex-desktop build`/`run`) need a native
  toolchain on their machine, because the build compiles a Tauri (Rust) shell from source —
  a `pip install` cannot ship a C/Rust compiler. This is the standard Tauri developer setup:

  | | Requirement |
  |---|---|
  | **All platforms** | Rust toolchain (`cargo`), via [rustup](https://rustup.rs) |
  | **Linux** | `libwebkit2gtk-4.1-dev`, `build-essential`, `libssl-dev`, `librsvg2-dev`, `libayatana-appindicator3-dev`, `curl`/`wget`/`file` |
  | **macOS** | Xcode Command Line Tools (`xcode-select --install`) |
  | **Windows** | Microsoft C++ Build Tools (MSVC) + the WebView2 runtime |
  | **`--bundle`** | the Tauri CLI: `cargo install tauri-cli --locked` |
  | **`embedded`** | network access on the first build (downloads a python-build-standalone interpreter and pip-installs the backend) |

  Exact package names vary by distro/version — see Tauri's
  [prerequisites](https://tauri.app/start/prerequisites/) for the source of truth.

Check your machine before building:

```bash
reflex-desktop doctor            # verify Rust + platform WebView deps
reflex-desktop doctor --bundle   # also verify the Tauri CLI (for installers)
```

`build`/`run` run these same checks first and abort with copy-pasteable install steps if a
required dependency is missing, rather than failing later with a cryptic linker error.

## Usage

```bash
uv pip install -e ignore/reflex-tauri --no-deps
```

In `rxconfig.py`:

```python
import reflex as rx
from reflex_desktop import DesktopPlugin

config = rx.Config(
    app_name="my_app",
    cors_allowed_origins=["*"],  # allow the Tauri webview origin
    plugins=[
        DesktopPlugin(backend="remote", backend_url="https://api.my_app.com"),
    ],
)
```

Then:

```bash
reflex-desktop run              # build the desktop app and launch it
reflex-desktop build            # build only (reflex export --frontend-only + cargo build --release)
reflex-desktop build --bundle   # also produce installers via the Tauri CLI
reflex-desktop run --skip-build # relaunch an already-built app
```

For `backend="embedded"`, `reflex-desktop build` additionally downloads a relocatable
interpreter, pip-installs the backend into `site-packages/`, copies the app payload, and
sets `PYO3_PYTHON` for the `cargo` build.

The Tauri project is scaffolded under `<app>/<tauri_dir>/` (default `tauri/`), with
`src-tauri/` and the copied static frontend in `dist/`.

## Customizing the window, icon & native APIs

`DesktopPlugin` is the source of truth for the generated `tauri.conf.json`, `capabilities/`,
and (extra) plugin deps — these are re-applied on **every** build (hand edits outside the
managed regions are preserved). Set them in `rxconfig.py`:

```python
DesktopPlugin(
    backend="embedded",
    window_width=1100, window_height=750,
    resizable=True, min_width=900, min_height=650, center=True,  # + fullscreen/decorations/
    theme="Dark",                                                #   transparent/always_on_top/maximized
    icon="assets/logo.png",          # copied over the bundle icons (PNG)
    with_global_tauri=True,          # default — exposes window.__TAURI__ for the bridge below
    tauri_plugins=("notification",), # crate tauri-plugin-<name>, injected + permissioned
    extra_capabilities=("os:default",),
)
```

**Icons:** `icon=` copies one PNG over the four bundle icons. For the full platform set
(`.ico`/`.icns` + every size) run `cd <tauri_dir>/src-tauri && cargo tauri icon <path>`.

**Native APIs from Reflex events** — `reflex_desktop.desktop` wraps `window.__TAURI__` so a
Python handler can drive the window/OS (`with_global_tauri=True` required, which it is by
default):

```python
from reflex_desktop import desktop

rx.button("minimize", on_click=desktop.minimize())
rx.button("max/restore", on_click=desktop.toggle_maximize())
rx.button("close", on_click=desktop.close())
rx.button("notify", on_click=desktop.notify("Title", "Body"))  # needs tauri_plugins=("notification",)
```

`desktop.notify()` requests OS notification permission before sending (a silent no-op
otherwise on macOS/Windows). On Linux a *bundled* app (`reflex-desktop build --bundle`, which
installs a `.desktop` entry) shows banners reliably; a bare dev binary may have notifications
collected in the desktop's notification tray instead of popped as a banner.

Anything the plugin doesn't expose can still be done by editing the generated `src-tauri/`
project directly — it's a normal Tauri project (add plugins needing a non-`init()` builder to
`main.rs` by hand, etc.).
