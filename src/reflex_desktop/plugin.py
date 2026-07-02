"""Reflex plugin that wires a Reflex build into a Tauri desktop shell."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Literal

from reflex.plugins import Plugin

from .config import DEFAULT_TAURI_DIR, LOOPBACK_HOST, default_port, slugify

BackendMode = Literal["remote", "embedded"]

# Tokens substituted into the copied scaffold's text files (Cargo.toml, main.rs).
_CRATE_NAME_TOKEN = "__CRATE_NAME__"
_PORT_TOKEN = "__PORT__"
# Records which backend mode a scaffold was generated for, so a later build can detect a
# config change and re-scaffold instead of silently reusing the wrong shell.
_BACKEND_MARKER = ".reflex-desktop-backend"
# Managed-region markers for idempotent injection of extra Tauri plugins.
_CARGO_REGION = ("# >>> reflex-desktop plugins >>>", "# <<< reflex-desktop plugins <<<")
_RS_REGION = ("    // >>> reflex-desktop plugins >>>", "    // <<< reflex-desktop plugins <<<")
# Managed region inside the Builder's setup closure for generated startup Rust (tray, ...).
_SETUP_REGION = (
    "            // >>> reflex-desktop setup >>>",
    "            // <<< reflex-desktop setup <<<",
)
# The scaffold's tauri dependency line, with and without the tray feature.
_TAURI_DEP_PLAIN = 'tauri = { version = "2" }'
_TAURI_DEP_TRAY = 'tauri = { version = "2", features = ["tray-icon"] }'
_TRAY_RS_TEMPLATE = """\
            {
                use tauri::Manager as _;
                let tray_menu = tauri::menu::MenuBuilder::new(app)
                    .item(
                        &tauri::menu::MenuItemBuilder::with_id("reflex-desktop-show", "Show")
                            .build(app)?,
                    )
                    .item(
                        &tauri::menu::MenuItemBuilder::with_id("reflex-desktop-quit", "Quit")
                            .build(app)?,
                    )
                    .build()?;
                let mut tray = tauri::tray::TrayIconBuilder::with_id("reflex-desktop-tray")
                    .menu(&tray_menu)
                    .tooltip(__TOOLTIP__)
                    .on_menu_event(|app, event| match event.id().as_ref() {
                        "reflex-desktop-show" => {
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.show();
                                let _ = window.unminimize();
                                let _ = window.set_focus();
                            }
                        }
                        "reflex-desktop-quit" => app.exit(0),
                        _ => {}
                    });
                if let Some(icon) = app.default_window_icon() {
                    tray = tray.icon(icon.clone());
                }
                tray.build(app)?;
            }"""
_NOTIFICATION_BRIDGE_DEP = 'notify-rust = "4"'
_NOTIFICATION_BRIDGE_HANDLER = (
    "        .invoke_handler(tauri::generate_handler![reflex_desktop_notify])"
)
_NOTIFICATION_BRIDGE_PERMISSION = "reflex-desktop-notify"
_NOTIFICATION_BRIDGE_PERMISSION_TOML = """[[permission]]
identifier = "reflex-desktop-notify"
description = "Allows Reflex event handlers to show notifications through the generated bridge."
commands.allow = ["reflex_desktop_notify"]
"""
_NOTIFICATION_HELPER_MAIN_GUARD = """    if reflex_desktop_run_notification_helper() {
        return;
    }
"""
_NOTIFICATION_BRIDGE_RS = """#[cfg(all(unix, not(target_os = "macos")))]
fn reflex_desktop_send_notification(
    app_name: &str,
    title: &str,
    body: Option<&str>,
) -> Result<(), String> {
    let mut notification = notify_rust::Notification::new();
    notification.appname(app_name).summary(title);
    if let Some(body) = body {
        notification.body(body);
    }

    notification
        .show()
        .map(|_| ())
        .map_err(|err| err.to_string())
}

#[cfg(all(unix, not(target_os = "macos")))]
fn reflex_desktop_run_notification_helper() -> bool {
    if !std::env::args().any(|arg| arg == "--reflex-desktop-notify-helper") {
        return false;
    }

    let app_name = std::env::var("REFLEX_DESKTOP_NOTIFY_APP_NAME")
        .unwrap_or_else(|_| "Reflex Notifications".to_string());
    let title = std::env::var("REFLEX_DESKTOP_NOTIFY_TITLE").unwrap_or_default();
    let body = std::env::var("REFLEX_DESKTOP_NOTIFY_BODY").ok();

    if reflex_desktop_send_notification(&app_name, &title, body.as_deref()).is_err() {
        std::process::exit(1);
    }
    true
}

#[cfg(not(all(unix, not(target_os = "macos"))))]
fn reflex_desktop_run_notification_helper() -> bool {
    false
}

#[tauri::command]
fn reflex_desktop_notify(
    app: tauri::AppHandle,
    title: String,
    body: Option<String>,
) -> Result<(), String> {
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let app_name = app
            .config()
            .product_name
            .clone()
            .unwrap_or_else(|| "Reflex".to_string());
        let notification_app_name = format!("{app_name} Notifications");
        let mut command =
            std::process::Command::new(std::env::current_exe().map_err(|err| err.to_string())?);
        command
            .arg("--reflex-desktop-notify-helper")
            .env("REFLEX_DESKTOP_NOTIFY_APP_NAME", &notification_app_name)
            .env("REFLEX_DESKTOP_NOTIFY_TITLE", &title);
        if let Some(body) = body {
            command.env("REFLEX_DESKTOP_NOTIFY_BODY", body);
        }

        return match command.status() {
            Ok(status) if status.success() => Ok(()),
            Ok(status) => {
                let err = format!("notification helper exited with {status}");
                Err(err)
            }
            Err(err) => Err(err.to_string()),
        };
    }

    #[cfg(not(all(unix, not(target_os = "macos"))))]
    {
        let _ = app;
        let err = "reflex-desktop notification bridge is Linux-only";
        let _ = (title, body);
        Err(err.to_string())
    }
}
"""


@dataclasses.dataclass(kw_only=True, frozen=True)
class DesktopPlugin(Plugin):
    """Package the compiled Reflex frontend into a Tauri desktop app.

    The plugin is the Reflex-side half of ``reflex-desktop``: it bakes the right backend
    URL into the static build (``update_env_json``) and, after the frontend is built,
    scaffolds a Tauri project and copies the static output into it (``post_build``).
    The ``reflex-desktop`` CLI drives the surrounding ``reflex export`` / ``cargo`` steps.

    Attributes:
        backend: How the desktop app reaches its backend. ``"remote"`` talks to a hosted
            URL; ``"embedded"`` runs the ASGI backend in-process on ``127.0.0.1:port``.
        backend_url: Base URL of the remote backend (``remote`` mode). When ``None`` the
            app's ``config.api_url`` is used unchanged.
        port: Loopback port the embedded backend binds to (``embedded`` mode). Defaults to
            a stable per-app port derived from the bundle identifier, so two installed
            reflex-desktop apps don't collide on a shared fixed port.
        product_name: Display name of the app. Defaults to the Reflex app name.
        identifier: Reverse-DNS bundle identifier. Defaults to ``dev.reflex.<app>``.
        window_title: Title of the main window. Defaults to ``product_name``.
        window_width: Initial window width in logical pixels.
        window_height: Initial window height in logical pixels.
        resizable: Whether the window can be resized. Left to Tauri's default if ``None``.
        fullscreen: Open fullscreen. Left to Tauri's default if ``None``.
        decorations: Show the OS title bar / borders (``False`` = frameless). Default if ``None``.
        transparent: Transparent window background. Default if ``None``.
        always_on_top: Keep the window above others. Default if ``None``.
        center: Center the window on launch. Default if ``None``.
        maximized: Start maximized. Default if ``None``.
        min_width: Minimum window width. Unset if ``None``.
        min_height: Minimum window height. Unset if ``None``.
        max_width: Maximum window width. Unset if ``None``.
        max_height: Maximum window height. Unset if ``None``.
        theme: Force a window theme (``"Light"`` / ``"Dark"``). System default if ``None``.
        icon: Path to a source image (PNG) copied over the bundle icons. For the full set of
            platform formats (``.ico``/``.icns`` + all sizes) run ``cargo tauri icon <path>``.
        with_global_tauri: Expose ``window.__TAURI__`` in the webview so Reflex event handlers
            can call native APIs (e.g. ``reflex_desktop.desktop.minimize()`` via
            ``rx.call_script``).
        tray: Add a system tray icon (the app icon) with a Show / Quit menu. On Linux the
            tray needs libayatana-appindicator at runtime (see ``reflex-desktop doctor``).
        tray_tooltip: Tray icon tooltip. Defaults to ``product_name``.
        tauri_plugins: Extra Tauri plugins to add (crate ``tauri-plugin-<name>``), injected into
            ``Cargo.toml``/``main.rs`` and granted ``<name>:default`` permission, e.g.
            ``("notification", "dialog")``. Plugins the scaffold already registers are only
            granted their permission. Plugins whose Rust init isn't ``init()`` (and isn't
            special-cased, like ``updater``) need a manual edit to ``main.rs``.
        updater_endpoints: Update-manifest URLs for ``tauri-plugin-updater`` (written to
            ``tauri.conf.json`` and enabling updater artifacts). Requires
            ``"updater"`` in ``tauri_plugins``; see docs/updater.md.
        updater_pubkey: The minisign public key updates must be signed with (from
            ``cargo tauri signer generate``). Required by the updater plugin.
        extra_capabilities: Additional capability permission strings to grant the main window.
        tauri_dir: Tauri project directory relative to the app root (holds ``src-tauri/``
            and the copied static frontend in ``dist/``).
    """

    backend: BackendMode = "remote"
    backend_url: str | None = None
    port: int | None = None
    product_name: str | None = None
    identifier: str | None = None
    window_title: str | None = None
    window_width: int = 1100
    window_height: int = 750
    resizable: bool | None = None
    fullscreen: bool | None = None
    decorations: bool | None = None
    transparent: bool | None = None
    always_on_top: bool | None = None
    center: bool | None = None
    maximized: bool | None = None
    min_width: int | None = None
    min_height: int | None = None
    max_width: int | None = None
    max_height: int | None = None
    theme: str | None = None
    icon: str | None = None
    with_global_tauri: bool = True
    tray: bool = False
    tray_tooltip: str | None = None
    tauri_plugins: tuple[str, ...] = ()
    updater_endpoints: tuple[str, ...] = ()
    updater_pubkey: str | None = None
    extra_capabilities: tuple[str, ...] = ()
    tauri_dir: str = DEFAULT_TAURI_DIR

    def _resolved_port(self) -> int:
        """Return the embedded backend port, deriving the per-app default when unset.

        Returns:
            The explicitly configured port, or a stable port hashed from the identifier.
        """
        if self.port is not None:
            return self.port
        return default_port(self._resolved_names()[1])

    def _backend_base(self) -> str | None:
        """Return the backend base URL to bake into ``env.json``.

        Returns:
            The base URL (no trailing slash), or ``None`` to leave ``config.api_url`` as-is.
        """
        if self.backend == "embedded":
            return f"http://{LOOPBACK_HOST}:{self._resolved_port()}"
        return self.backend_url.rstrip("/") if self.backend_url else None

    def update_env_json(self, **context) -> dict[str, str] | None:
        """Rebuild the baked backend endpoint URLs against the chosen backend base.

        Mirrors ``reflex_base.constants.event.Endpoint.get_url`` but against this
        plugin's base instead of ``config.api_url`` — letting embedded mode pin
        ``127.0.0.1`` (dodging the ``state.js`` localhost→hostname rewrite) and remote
        mode point at a hosted URL.

        Args:
            context: Unused plugin context.

        Returns:
            A mapping of endpoint name to URL, or ``None`` to contribute nothing.
        """
        from reflex_base.config import get_config
        from reflex_base.constants.event import Endpoint

        base = self._backend_base()
        if base is None:
            return None

        config = get_config()
        # Apply the configured backend_path prefix when a config is available; the
        # default prefix is empty, so the identity fallback matches it for plain apps.
        prepend = config.prepend_backend_path if config is not None else (lambda path: path)
        env: dict[str, str] = {}
        for endpoint in Endpoint:
            url = base + prepend(str(endpoint))
            if endpoint == Endpoint.EVENT:
                url = url.replace("https://", "wss://").replace("http://", "ws://")
            env[endpoint.name] = url
        return env

    def post_build(self, **context) -> None:
        """Scaffold the Tauri project (if missing) and copy in the static frontend.

        Args:
            context: Plugin context; ``static_dir`` is the built frontend
                (``.web/build/client``).
        """
        from reflex_base.utils import console

        static_dir = Path(context["static_dir"]).resolve()
        project_root = (Path.cwd() / self.tauri_dir).resolve()
        src_tauri = project_root / "src-tauri"
        dist = project_root / "dist"

        if not src_tauri.exists():
            self._scaffold(src_tauri)
            console.info(
                f"reflex-desktop: scaffolded {self.backend} Tauri project at {project_root}"
            )
        elif self._existing_backend(src_tauri) == self.backend:
            console.info(
                f"reflex-desktop: reusing existing {self.backend} Tauri project at {project_root}"
            )
        else:
            existing = self._existing_backend(src_tauri) or "unknown"
            console.info(
                f"reflex-desktop: backend is {self.backend!r} but the scaffold at "
                f"{project_root} is {existing!r}; re-scaffolding the Tauri shell."
            )
            self._scaffold(src_tauri)

        # Apply config on every build so rxconfig is the source of truth for window options,
        # icon, capabilities and extra plugins (managed regions are rewritten idempotently;
        # hand edits outside them are preserved).
        self._configure(src_tauri)

        if dist.exists():
            shutil.rmtree(dist)
        shutil.copytree(static_dir, dist)
        console.info(f"reflex-desktop: copied static frontend into {dist}")

        self._warn_if_cors_blocks()

    def _existing_backend(self, src_tauri: Path) -> str | None:
        """Return the backend mode a pre-existing scaffold was generated for.

        Args:
            src_tauri: An existing ``src-tauri`` directory.

        Returns:
            The recorded backend mode, or ``None`` for a scaffold without a marker.
        """
        marker = src_tauri / _BACKEND_MARKER
        return marker.read_text().strip() if marker.exists() else None

    # Webview origins Tauri serves the bundled frontend from (platform-dependent).
    _TAURI_ORIGINS = ("tauri://localhost", "http://tauri.localhost")

    def _warn_if_cors_blocks(self) -> None:
        """Warn when the app's CORS config would block the Tauri webview origin.

        The embedded backend is reached cross-origin from the ``tauri://localhost`` /
        ``http://tauri.localhost`` webview, so the socket.io connection silently fails
        unless those origins (or ``*``) are allowed.
        """
        if self.backend != "embedded":
            return

        from reflex_base.config import get_config
        from reflex_base.utils import console

        config = get_config()
        if config is None:
            return
        origins = tuple(config.cors_allowed_origins)
        if "*" in origins or any(o in origins for o in self._TAURI_ORIGINS):
            return
        console.warn(
            "reflex-desktop: cors_allowed_origins does not include the Tauri webview origin "
            f"({' or '.join(self._TAURI_ORIGINS)}). The desktop app's backend connection "
            'may be blocked. Set cors_allowed_origins=["*"] or add those origins in rxconfig.'
        )

    def _scaffold(self, src_tauri: Path) -> None:
        """Copy the bundled scaffold for this backend mode and apply app settings.

        Overlays the template (``dirs_exist_ok``) so a re-scaffold on a backend change
        replaces the shell's source files while preserving build artifacts assembled
        alongside it (``python/``, ``site-packages/``, ``app/``, ``target/``).

        Args:
            src_tauri: Destination ``src-tauri`` directory to create or refresh.
        """
        template = Path(__file__).parent / "scaffold" / self.backend / "src-tauri"
        if not template.is_dir():
            msg = f"reflex-desktop: missing scaffold template at {template}"
            raise FileNotFoundError(msg)

        shutil.copytree(template, src_tauri, dirs_exist_ok=True)
        self._substitute_tokens(src_tauri, slugify(self._resolved_names()[0]))
        (src_tauri / _BACKEND_MARKER).write_text(self.backend + "\n")
        self._write_gitignore(src_tauri.parent)

    def _configure(self, src_tauri: Path) -> None:
        """Apply rxconfig-driven settings to the (existing) scaffold, idempotently.

        Runs on every build: patches ``tauri.conf.json`` (names, window options,
        ``withGlobalTauri``), refreshes the bundle icon, rewrites the window capabilities,
        injects the Reflex notification bridge, and injects any extra Tauri plugins.

        Args:
            src_tauri: The ``src-tauri`` directory.
        """
        product_name, identifier, window_title = self._resolved_names()
        self._apply_conf(src_tauri / "tauri.conf.json", product_name, identifier, window_title)
        self._apply_icon(src_tauri)
        self._apply_capabilities(src_tauri / "capabilities" / "default.json")
        self._apply_plugins(src_tauri)
        self._apply_notification_bridge(src_tauri)
        self._apply_tray(src_tauri, product_name)

    def _write_gitignore(self, project_root: Path) -> None:
        """Write a ``.gitignore`` for the generated Tauri build artifacts.

        Args:
            project_root: The Tauri project root (holds ``src-tauri/`` and ``dist/``).
        """
        entries = ["/dist/", "/src-tauri/target/"]
        if self.backend == "embedded":
            # Resources assembled at build time by the reflex-desktop CLI (runtime.assemble).
            entries += ["/src-tauri/python/", "/src-tauri/site-packages/", "/src-tauri/app/"]
        (project_root / ".gitignore").write_text("\n".join(entries) + "\n")

    def _resolved_names(self) -> tuple[str, str, str]:
        """Resolve product name / identifier / window title, filling defaults from config.

        The Reflex app name is only read when a default is actually needed.

        Returns:
            A ``(product_name, identifier, window_title)`` tuple.
        """
        app_name = None
        if self.product_name is None or self.identifier is None:
            from reflex_base.config import get_config

            app_name = get_config().app_name
        product_name = self.product_name or app_name or "reflex-app"
        identifier = (
            self.identifier or f"dev.reflex.{re.sub(r'[^a-z0-9]', '', (app_name or 'app').lower())}"
        )
        window_title = self.window_title or product_name
        return product_name, identifier, window_title

    def _apply_conf(
        self, conf_path: Path, product_name: str, identifier: str, window_title: str
    ) -> None:
        """Patch the scaffold's ``tauri.conf.json`` with this app's settings.

        Args:
            conf_path: Path to the ``tauri.conf.json`` to edit in place.
            product_name: Display name of the app.
            identifier: Reverse-DNS bundle identifier.
            window_title: Title of the main window.
        """
        conf = json.loads(conf_path.read_text())
        conf["productName"] = product_name
        conf["identifier"] = identifier
        app = conf.setdefault("app", {})
        app["withGlobalTauri"] = self.with_global_tauri
        window = app.setdefault("windows", [{}])[0]
        window["title"] = window_title
        window["width"] = self.window_width
        window["height"] = self.window_height
        # Optional window props are written only when set, so unset ones keep Tauri's defaults
        # (and any hand edits to tauri.conf.json survive).
        optional = {
            "resizable": self.resizable,
            "fullscreen": self.fullscreen,
            "decorations": self.decorations,
            "transparent": self.transparent,
            "alwaysOnTop": self.always_on_top,
            "center": self.center,
            "maximized": self.maximized,
            "minWidth": self.min_width,
            "minHeight": self.min_height,
            "maxWidth": self.max_width,
            "maxHeight": self.max_height,
            "theme": self.theme,
        }
        for key, value in optional.items():
            if value is not None:
                window[key] = value
        if self.backend == "embedded":
            # Windows resolves the exe's load-time python3XY.dll import from the exe's own
            # directory, so map the bundled DLLs next to it. Managed per-platform because
            # tauri-build fails the build on a resource glob with no matches.
            resources = conf.setdefault("bundle", {}).setdefault("resources", {})
            dll_glob = "python/python/python3*.dll"
            if sys.platform in ("win32", "cygwin"):
                resources[dll_glob] = "./"
            else:
                resources.pop(dll_glob, None)
        if "updater" in self.tauri_plugins:
            updater = conf.setdefault("plugins", {}).setdefault("updater", {})
            if self.updater_endpoints:
                updater["endpoints"] = list(self.updater_endpoints)
            if self.updater_pubkey:
                updater["pubkey"] = self.updater_pubkey
            # cargo tauri build then emits the signed artifacts the updater consumes.
            conf.setdefault("bundle", {})["createUpdaterArtifacts"] = True
        conf_path.write_text(json.dumps(conf, indent=2) + "\n")

    def _apply_icon(self, src_tauri: Path) -> None:
        """Copy the configured source image over the bundle's icon files.

        Args:
            src_tauri: The ``src-tauri`` directory.
        """
        if not self.icon:
            return
        from reflex_base.utils import console

        source = Path(self.icon)
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if not source.is_file():
            console.warn(f"reflex-desktop: icon {source} not found; keeping the placeholder icons.")
            return
        icons_dir = src_tauri / "icons"
        icons_dir.mkdir(parents=True, exist_ok=True)
        for name in ("32x32.png", "128x128.png", "128x128@2x.png", "icon.png"):
            shutil.copyfile(source, icons_dir / name)
        console.info(
            f"reflex-desktop: applied icon {source.name}; for full platform icons "
            "(.ico/.icns + every size) run `cargo tauri icon <path>` in src-tauri."
        )

    # Core window commands the rx<->Tauri bridge invokes; granted when withGlobalTauri is on.
    _BRIDGE_PERMISSIONS = (
        "core:window:allow-minimize",
        "core:window:allow-maximize",
        "core:window:allow-unmaximize",
        "core:window:allow-toggle-maximize",
        "core:window:allow-close",
        "core:window:allow-set-fullscreen",
        "core:window:allow-set-title",
        "core:window:allow-start-dragging",
    )

    def _apply_capabilities(self, cap_path: Path) -> None:
        """Rewrite the main window's capability permissions from rxconfig.

        Grants ``core:default``, the app notification bridge and bridge window permissions
        (when ``with_global_tauri``), ``<name>:default`` for each extra Tauri plugin, and
        any ``extra_capabilities``.

        Args:
            cap_path: Path to ``capabilities/default.json``.
        """
        if cap_path.exists():
            cap = json.loads(cap_path.read_text())
        else:
            cap = {
                "$schema": "../gen/schemas/desktop-schema.json",
                "identifier": "default",
                "description": "Default capability for the main window.",
                "windows": ["main"],
            }
        perms = ["core:default"]
        if self.with_global_tauri:
            perms.append(_NOTIFICATION_BRIDGE_PERMISSION)
            perms += list(self._BRIDGE_PERMISSIONS)
        perms += [f"{name}:default" for name in self.tauri_plugins]
        perms += list(self.extra_capabilities)
        seen: set[str] = set()
        cap["permissions"] = [p for p in perms if not (p in seen or seen.add(p))]
        cap_path.parent.mkdir(parents=True, exist_ok=True)
        cap_path.write_text(json.dumps(cap, indent=2) + "\n")

    # Plugins whose Rust registration is not the conventional ``init()``.
    _PLUGIN_INIT_OVERRIDES = {
        "updater": "tauri_plugin_updater::Builder::new().build()",
    }

    def _apply_plugins(self, src_tauri: Path) -> None:
        """Inject the configured extra Tauri plugins into ``Cargo.toml`` and ``main.rs``.

        Writes a managed region (between marker comments) in each file so the set is rewritten
        idempotently on every build and edits outside the region are preserved. Plugins the
        scaffold (or a hand edit) already wires up outside the region — e.g. ``dialog`` and
        ``single-instance`` in the embedded shell — are skipped, so requesting them in
        ``tauri_plugins`` only grants their capability instead of double-registering.

        Args:
            src_tauri: The ``src-tauri`` directory.
        """
        cargo = src_tauri / "Cargo.toml"
        main_rs = src_tauri / "src" / "main.rs"

        if cargo.exists():
            text = cargo.read_text()
            baseline = self._without_region(text, _CARGO_REGION[0], _CARGO_REGION[1])
            deps = "\n".join(
                f'tauri-plugin-{name} = "2"'
                for name in self.tauri_plugins
                if not re.search(rf"(?m)^tauri-plugin-{re.escape(name)}\s*=", baseline)
            )
            cargo.write_text(
                self._set_region(text, _CARGO_REGION[0], _CARGO_REGION[1], "[dependencies]", deps)
            )

        if main_rs.exists():
            text = main_rs.read_text()
            baseline = self._without_region(text, _RS_REGION[0], _RS_REGION[1])
            registrations = "\n".join(
                f"        .plugin({self._plugin_init(name)})"
                for name in self.tauri_plugins
                if f"tauri_plugin_{name.replace('-', '_')}::" not in baseline
            )
            main_rs.write_text(
                self._set_region(
                    text,
                    _RS_REGION[0],
                    _RS_REGION[1],
                    "tauri::Builder::default()",
                    registrations,
                )
            )

    def _apply_tray(self, src_tauri: Path, product_name: str) -> None:
        """Write the generated tray-icon Rust into the managed setup region (idempotent).

        With ``tray=False`` the region is emptied and the ``tray-icon`` cargo feature is
        dropped again, so toggling the option round-trips cleanly.

        Args:
            src_tauri: The ``src-tauri`` directory.
            product_name: Resolved product name (default tooltip).
        """
        main_rs = src_tauri / "src" / "main.rs"
        if not main_rs.exists():
            return
        tooltip = json.dumps(self.tray_tooltip or product_name, ensure_ascii=False)
        body = _TRAY_RS_TEMPLATE.replace("__TOOLTIP__", tooltip) if self.tray else ""
        text = main_rs.read_text()
        if _SETUP_REGION[0] not in text and ".setup(" not in text:
            # Scaffold predating the setup hook: add one ahead of the run() call.
            text = text.replace(
                "        .run(tauri::generate_context!())",
                "        .setup(|app| {\n"
                f"{_SETUP_REGION[0]}\n{_SETUP_REGION[1]}\n"
                "            let _ = app;\n"
                "            Ok(())\n"
                "        })\n"
                "        .run(tauri::generate_context!())",
                1,
            )
        main_rs.write_text(
            self._set_region(text, _SETUP_REGION[0], _SETUP_REGION[1], ".setup(|app| {", body)
        )

        cargo = src_tauri / "Cargo.toml"
        if not cargo.exists():
            return
        cargo_text = cargo.read_text()
        if self.tray:
            updated = cargo_text.replace(_TAURI_DEP_PLAIN, _TAURI_DEP_TRAY, 1)
            if "tray-icon" not in updated:
                from reflex_base.utils import console

                console.warn(
                    "reflex-desktop: could not enable the `tray-icon` cargo feature "
                    "automatically (custom tauri dependency line in Cargo.toml?); add "
                    '`features = ["tray-icon"]` to the tauri dependency by hand.'
                )
            cargo_text = updated
        else:
            cargo_text = cargo_text.replace(_TAURI_DEP_TRAY, _TAURI_DEP_PLAIN, 1)
        cargo.write_text(cargo_text)

    @classmethod
    def _plugin_init(cls, name: str) -> str:
        """Return the Rust expression that registers a Tauri plugin.

        Args:
            name: The plugin's crate suffix (e.g. ``"dialog"`` for ``tauri-plugin-dialog``).

        Returns:
            The registration expression to pass to ``.plugin(...)``.
        """
        snake = name.replace("-", "_")
        return cls._PLUGIN_INIT_OVERRIDES.get(name, f"tauri_plugin_{snake}::init()")

    @staticmethod
    def _without_region(text: str, begin: str, end: str) -> str:
        """Return ``text`` with the managed region (markers included) removed.

        Args:
            text: The file contents.
            begin: Region begin marker line.
            end: Region end marker line.

        Returns:
            The contents outside the managed region.
        """
        if begin in text and end in text:
            return text[: text.index(begin)] + text[text.index(end) + len(end) :]
        return text

    def _apply_notification_bridge(self, src_tauri: Path) -> None:
        """Ensure reused scaffolds include the Reflex notification command.

        Args:
            src_tauri: The ``src-tauri`` directory.
        """
        cargo = src_tauri / "Cargo.toml"
        if cargo.exists():
            cargo_text = cargo.read_text()
            if not re.search(r"(?m)^notify-rust\s*=", cargo_text):
                cargo.write_text(
                    cargo_text.replace(
                        "[dependencies]\n",
                        f"[dependencies]\n{_NOTIFICATION_BRIDGE_DEP}\n",
                        1,
                    )
                )

        permission = src_tauri / "permissions" / "reflex-desktop.toml"
        permission.parent.mkdir(parents=True, exist_ok=True)
        if (
            not permission.exists()
            or permission.read_text() != _NOTIFICATION_BRIDGE_PERMISSION_TOML
        ):
            permission.write_text(_NOTIFICATION_BRIDGE_PERMISSION_TOML)

        main_rs = src_tauri / "src" / "main.rs"
        if not main_rs.exists():
            return
        text = self._replace_notification_bridge_commands(main_rs.read_text())
        text, replaced = re.subn(
            r"\n\s*\.invoke_handler\(tauri::generate_handler!\[\s*"
            r"(?:reflex_desktop_terminal_log\s*,\s*)?reflex_desktop_notify\s*\]\)",
            f"\n{_NOTIFICATION_BRIDGE_HANDLER}",
            text,
            count=1,
        )
        if replaced == 0:
            text = text.replace(
                "tauri::Builder::default()",
                f"tauri::Builder::default()\n{_NOTIFICATION_BRIDGE_HANDLER}",
                1,
            )
        if "reflex_desktop_run_notification_helper() {" not in text:
            text = text.replace(
                "fn main() {\n",
                f"fn main() {{\n{_NOTIFICATION_HELPER_MAIN_GUARD}\n",
                1,
            )
        main_rs.write_text(text)

    @staticmethod
    def _replace_notification_bridge_commands(text: str) -> str:
        """Replace or insert the generated notification bridge commands.

        Args:
            text: Existing ``main.rs`` contents.

        Returns:
            ``main.rs`` contents with the current generated bridge commands.
        """
        positions = [
            pos
            for marker in (
                "#[tauri::command]\nfn reflex_desktop_terminal_log",
                '#[cfg(all(unix, not(target_os = "macos")))]\nfn reflex_desktop_send_notification',
                "#[tauri::command]\nfn reflex_desktop_notify",
            )
            if (pos := text.find(marker)) != -1
        ]
        if positions:
            start = min(positions)
            anchors = [
                pos
                for marker in ("\nfn port_available", "\nfn main()")
                if (pos := text.find(marker, start)) != -1
            ]
            if anchors:
                return f"{text[:start]}{_NOTIFICATION_BRIDGE_RS}{text[min(anchors) :]}"
            return text

        if "\nfn main()" in text:
            text = text.replace(
                "\nfn main()",
                f"\n{_NOTIFICATION_BRIDGE_RS}\nfn main()",
                1,
            )
        return text

    @staticmethod
    def _set_region(text: str, begin: str, end: str, anchor: str, body: str) -> str:
        """Replace a marked region's body, or insert the region after ``anchor`` if absent.

        Args:
            text: The file contents.
            begin: Region begin marker line.
            end: Region end marker line.
            anchor: Substring to insert the region after when no markers exist yet.
            body: New region body (may be empty).

        Returns:
            The updated file contents.
        """
        region = f"{begin}\n{body}\n{end}" if body else f"{begin}\n{end}"
        if begin in text and end in text:
            return text[: text.index(begin)] + region + text[text.index(end) + len(end) :]
        cut = text.index(anchor) + len(anchor)
        return f"{text[:cut]}\n{region}{text[cut:]}"

    def _substitute_tokens(self, src_tauri: Path, crate_name: str) -> None:
        """Replace scaffold tokens in ``Cargo.toml`` and ``src/main.rs``.

        Args:
            src_tauri: The scaffolded ``src-tauri`` directory.
            crate_name: Cargo-safe slug for the crate / binary name.
        """
        for rel in ("Cargo.toml", "src/main.rs"):
            path = src_tauri / rel
            if not path.exists():
                continue
            text = path.read_text()
            text = text.replace(_CRATE_NAME_TOKEN, crate_name)
            text = text.replace(_PORT_TOKEN, str(self._resolved_port()))
            path.write_text(text)
