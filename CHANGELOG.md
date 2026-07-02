# Changelog

## 0.2.0 (unreleased)

The "shippable installers" release: embedded bundles now run on machines other than the
one that built them, plus a real dev loop and the most-requested desktop primitives.

### Fixed

- **Embedded bundles are relocatable.** The shell now resolves the bundled libpython
  through install-layout-relative paths instead of an absolute build-machine rpath:
  `$ORIGIN/../lib/<app>/...` on Linux (deb/rpm/AppImage), `@rpath` +
  `@executable_path/../Resources/...` on macOS (the bundled dylib's install name is
  rewritten at assemble time and re-signed ad-hoc), and exe-adjacent `python3*.dll` on
  Windows (`reflex-desktop run` also puts the interpreter dir on `PATH` for dev binaries
  in `target/`).
- **Embedded backend dependencies are pinned.** `reflex` and `uvicorn` are installed into
  the bundle at the exact versions present in the build environment, so the baked
  frontend and the embedded backend can no longer drift apart.
- **Per-app default port.** The embedded backend's default port is derived from the app's
  bundle identifier instead of a shared fixed 8513, so two installed reflex-desktop apps
  don't collide. A busy port now surfaces a native error dialog instead of a silent
  stderr exit (same-app relaunches still just focus the running window).
- **`uv.lock` regenerated** so current `uv` releases can parse it (root and example).

### Added

- **`reflex-desktop dev`** — hot reload inside the real Tauri webview: starts the
  `reflex run` dev server and opens a plain dev shell (scaffolded into `<tauri_dir>-dev`)
  pointed at it, so `window.__TAURI__` bridge features work while iterating.
- **`DesktopPlugin(tray=True, tray_tooltip=...)`** — a system tray icon (the app icon)
  with a Show/Quit menu, generated into a managed region of `main.rs`.
- **Auto-updater wiring** — `tauri_plugins=("updater",)` +
  `updater_endpoints`/`updater_pubkey` registers `tauri-plugin-updater`, writes its
  config, and enables signed updater artifacts. Docs: `docs/updater.md`; code
  signing/notarization: `docs/signing.md`.
- **`desktop.invoke(command, args, callback=...)`** — call custom Tauri commands from
  event handlers, converting arg names to the camelCase Tauri expects on the wire
  (the classic silent-failure pitfall); `convert_keys=False` for
  `rename_all = "snake_case"` commands.
- **`desktop.open_file` / `desktop.save_file`** — native file dialogs (dialog plugin).
- **`desktop.clipboard_write` / `desktop.clipboard_read`** — system clipboard
  (clipboard-manager plugin).
- **CI** — pytest on ubuntu/macos/windows plus an end-to-end desktop build of the example
  app on all three platforms; on Linux the built `.deb` is installed and the embedded
  backend is booted from the installed location (the relocation acid test). A tag-driven
  PyPI release workflow (trusted publishing) is included.

### Changed

- Embedded bundles are trimmed: the stdlib test suite, IDLE, tkinter/Tcl/Tk trees,
  bytecode caches, and pip's script shims no longer ship.
- `tauri_plugins` entries the scaffold already registers (e.g. `dialog`,
  `single-instance` in the embedded shell) are no longer double-registered — requesting
  them just grants their capability.
- Scaffold `Cargo.toml` now includes `serde`/`serde_json` (required by
  `tauri::generate_context!` with plugin config, and handy for custom commands).

## 0.1.0

Initial scaffold: `DesktopPlugin` (remote + embedded PyO3 backend), `reflex-desktop`
CLI (`build`, `run`, `doctor`), window/icon/capability config, notification bridge.
