# reflex-desktop example: Counter

A minimal Reflex app (a backend-state counter) packaged as a Tauri desktop app, to build
and run the `reflex-desktop` plugin end-to-end.

This is a **standalone** project: it pulls Reflex from PyPI and only the (unpublished)
`reflex-desktop` plugin from the local checkout (see `pyproject.toml`). It has its own venv —
no dependency on the parent repo's dev environment.

## Build & run (embedded — default)

The Python backend runs **inside** the desktop binary via PyO3. There is no separate
server: just build and launch.

```bash
cd ignore/reflex-tauri/example
uv sync                     # PyPI reflex + editable reflex-desktop -> ./.venv

uv run reflex-desktop run     # build (debug) + launch in one step (backend runs in-process)
```

`run` builds then launches the binary. It defaults to a **debug** build for fast iteration
(like `cargo run`); pass `--release` for an optimized build. Use `uv run reflex-desktop build`
(release) to build only, and `uv run reflex-desktop run --skip-build [--release]` to relaunch an
already-built app without recompiling.

The embedded backend serves on `127.0.0.1:8513` in-process via PyO3 — verified headlessly
(`/_health` + the socket.io `/_event` WebSocket). Clicking `+`/`−` round-trips Python `State`
over that socket.

`reflex-desktop build` (embedded) additionally downloads a relocatable interpreter
(python-build-standalone), pip-installs the backend (`reflex` + the app's
`requirements.txt`) into the bundle, copies the app payload, and sets `PYO3_PYTHON` for the
`cargo` build. The binary serves the backend on `127.0.0.1:8513`.

Click `+` / `−`: the count is held in Python `State`, so each click round-trips over the
socket to the embedded backend and back.

## Alternative: remote mode

Edit `rxconfig.py` to use the `remote` plugin (commented in the file). Then the app is
frontend-only and needs a backend running separately:

```bash
uv run reflex-desktop build                 # build the frontend-only shell
uv run reflex run --backend-only          # the backend (leave running in this shell)
uv run reflex-desktop run --skip-build      # launch the already-built window
```

## Notes

- First `uv run reflex-desktop build` also runs the frontend setup (bun install into `.web/`)
  and, for embedded, downloads the interpreter — so it takes a few minutes; later builds are fast.
- Speed up `cargo` by reusing the repo's prebuilt Tauri dep cache:
  `export CARGO_TARGET_DIR="$(git rev-parse --show-toplevel)/examples/landing_page/tauri-hello/src-tauri/target"`
  (then the binary is at `$CARGO_TARGET_DIR/release/reflex-counter`).
- The dev binary runs in place. Shipping a *relocated* bundle (AppImage/.app) still needs the
  `$ORIGIN`-relative libpython rpath / signing work (M2, see the package README).
