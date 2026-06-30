"""Unit tests for the reflex-desktop plugin (no Tauri/Rust required)."""

from __future__ import annotations

import json

from reflex_desktop import DesktopPlugin


def test_update_env_json_embedded_pins_loopback():
    """Embedded mode bakes 127.0.0.1 (not localhost) so state.js won't rewrite the host."""
    env = DesktopPlugin(backend="embedded", port=8513).update_env_json()
    assert env is not None
    assert env["PING"] == "http://127.0.0.1:8513/ping"
    assert env["HEALTH"] == "http://127.0.0.1:8513/_health"
    # The event endpoint is a websocket.
    assert env["EVENT"] == "ws://127.0.0.1:8513/_event"
    assert all("localhost" not in url for url in env.values())


def test_update_env_json_custom_port():
    """The embedded port flows into every baked endpoint."""
    env = DesktopPlugin(backend="embedded", port=9001).update_env_json()
    assert env is not None
    assert env["EVENT"] == "ws://127.0.0.1:9001/_event"


def test_update_env_json_remote_url_uses_wss():
    """Remote mode rebases endpoints on the backend URL, upgrading the event to wss."""
    env = DesktopPlugin(backend="remote", backend_url="https://api.example.com/").update_env_json()
    assert env is not None
    assert env["PING"] == "https://api.example.com/ping"
    assert env["EVENT"] == "wss://api.example.com/_event"


def test_update_env_json_remote_without_url_is_noop():
    """Remote mode without a URL leaves config.api_url untouched (contributes nothing)."""
    assert DesktopPlugin(backend="remote").update_env_json() is None


def test_post_build_scaffolds_and_copies(tmp_path, monkeypatch):
    """post_build scaffolds src-tauri, patches the conf, and copies the static build."""
    static_dir = tmp_path / ".web" / "build" / "client"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html><title>app</title>")
    (static_dir / "assets" / "app.js").write_text("console.log(1)")

    monkeypatch.chdir(tmp_path)
    plugin = DesktopPlugin(
        backend="remote",
        product_name="My Cool App",
        identifier="dev.reflex.mycool",
        window_width=800,
        window_height=600,
    )
    plugin.post_build(static_dir=static_dir)

    project = tmp_path / "tauri"
    src_tauri = project / "src-tauri"
    assert (src_tauri / "src" / "main.rs").exists()
    assert (src_tauri / "build.rs").exists()
    assert (src_tauri / "capabilities" / "default.json").exists()

    conf = json.loads((src_tauri / "tauri.conf.json").read_text())
    assert conf["productName"] == "My Cool App"
    assert conf["identifier"] == "dev.reflex.mycool"
    assert conf["build"]["frontendDist"] == "../dist"
    window = conf["app"]["windows"][0]
    assert (window["width"], window["height"]) == (800, 600)
    assert window["title"] == "My Cool App"

    cargo = (src_tauri / "Cargo.toml").read_text()
    assert "__CRATE_NAME__" not in cargo
    assert 'name = "my-cool-app"' in cargo

    dist = project / "dist"
    assert (dist / "index.html").read_text().startswith("<!doctype html>")
    assert (dist / "assets" / "app.js").exists()


def test_post_build_reuses_existing_project_and_refreshes_dist(tmp_path, monkeypatch):
    """A second build keeps a hand-edited src-tauri but refreshes dist from the new build."""
    static_dir = tmp_path / ".web" / "build" / "client"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("v1")

    monkeypatch.chdir(tmp_path)
    plugin = DesktopPlugin(backend="remote", product_name="App", identifier="dev.reflex.app")
    plugin.post_build(static_dir=static_dir)

    # User customization should survive a rebuild.
    marker = tmp_path / "tauri" / "src-tauri" / "MARKER"
    marker.write_text("keep me")

    (static_dir / "index.html").write_text("v2")
    plugin.post_build(static_dir=static_dir)

    assert marker.exists()
    assert (tmp_path / "tauri" / "dist" / "index.html").read_text() == "v2"


def test_post_build_upgrades_existing_project_with_notification_bridge(tmp_path, monkeypatch):
    """A reused scaffold receives the Reflex notification bridge on later builds."""
    static_dir = tmp_path / ".web" / "build" / "client"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("x")
    monkeypatch.chdir(tmp_path)
    plugin = DesktopPlugin(backend="remote", product_name="App", identifier="dev.reflex.app")
    plugin.post_build(static_dir=static_dir)

    src_tauri = tmp_path / "tauri" / "src-tauri"
    cargo = src_tauri / "Cargo.toml"
    cargo.write_text(cargo.read_text().replace('notify-rust = "4"\n', ""))

    permissions = src_tauri / "permissions" / "reflex-desktop.toml"
    permissions.unlink(missing_ok=True)
    capabilities = src_tauri / "capabilities" / "default.json"
    cap = json.loads(capabilities.read_text())
    cap["permissions"] = [p for p in cap["permissions"] if p != "reflex-desktop-notify"]
    capabilities.write_text(json.dumps(cap, indent=2) + "\n")

    main_rs = src_tauri / "src" / "main.rs"
    text = main_rs.read_text()
    start = text.index("#[tauri::command]")
    end = text.index("\nfn main()")
    text = text[:start] + text[end + 1 :]
    text = text.replace(
        "        .invoke_handler(tauri::generate_handler![reflex_desktop_notify])\n", ""
    )
    text = text.replace(
        "        .invoke_handler(tauri::generate_handler![reflex_desktop_terminal_log, "
        "reflex_desktop_notify])\n",
        "",
    )
    main_rs.write_text(text)

    plugin.post_build(static_dir=static_dir)

    assert 'notify-rust = "4"' in cargo.read_text()
    assert 'identifier = "reflex-desktop-notify"' in permissions.read_text()
    assert 'commands.allow = ["reflex_desktop_notify"]' in permissions.read_text()
    assert "reflex_desktop_terminal_log" not in permissions.read_text()
    assert "reflex-desktop-notify" in json.loads(capabilities.read_text())["permissions"]
    main_text = main_rs.read_text()
    assert "fn reflex_desktop_run_notification_helper" in main_text
    assert "fn reflex_desktop_notify" in main_text
    assert "// >>> reflex-desktop commands >>>" in main_text
    assert "reflex_desktop_notify," in main_text
    assert main_text.count(".invoke_handler(") == 1
    assert "reflex_desktop_terminal_log" not in main_text
    assert "reflex-desktop notify:" not in main_text
    assert "notify helper:" not in main_text
    assert "--reflex-desktop-notify-helper" in main_text
    assert 'format!("{app_name} Notifications")' in main_text


def test_post_build_rescaffolds_on_backend_change(tmp_path, monkeypatch):
    """Switching backend mode replaces a stale scaffold instead of silently reusing it."""
    static_dir = tmp_path / ".web" / "build" / "client"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("x")
    monkeypatch.chdir(tmp_path)

    DesktopPlugin(backend="remote", product_name="App", identifier="dev.reflex.app").post_build(
        static_dir=static_dir
    )
    src_tauri = tmp_path / "tauri" / "src-tauri"
    assert "pyo3" not in (src_tauri / "Cargo.toml").read_text()
    assert (src_tauri / ".reflex-desktop-backend").read_text().strip() == "remote"

    # Switching to embedded must regenerate the shell as the PyO3 backend, not reuse remote.
    DesktopPlugin(backend="embedded", product_name="App", identifier="dev.reflex.app").post_build(
        static_dir=static_dir
    )
    assert "pyo3" in (src_tauri / "Cargo.toml").read_text()
    assert (src_tauri / ".reflex-desktop-backend").read_text().strip() == "embedded"


def test_embedded_scaffold_substitutes_port(tmp_path, monkeypatch):
    """Embedded mode scaffolds the PyO3 template with the configured port substituted."""
    static_dir = tmp_path / ".web" / "build" / "client"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("x")
    monkeypatch.chdir(tmp_path)

    plugin = DesktopPlugin(
        backend="embedded",
        port=9123,
        product_name="App",
        identifier="dev.reflex.app",
    )
    plugin.post_build(static_dir=static_dir)

    main_rs = (tmp_path / "tauri" / "src-tauri" / "src" / "main.rs").read_text()
    assert "__PORT__" not in main_rs
    assert "const BACKEND_PORT: u16 = 9123;" in main_rs
    cargo = (tmp_path / "tauri" / "src-tauri" / "Cargo.toml").read_text()
    assert "pyo3" in cargo


def test_scaffold_includes_reflex_notification_bridge(tmp_path, monkeypatch):
    """Scaffolds include the Reflex notification command used on GNOME."""
    src = _build_remote(tmp_path, monkeypatch)
    cargo = (src / "Cargo.toml").read_text()
    main_rs = (src / "src" / "main.rs").read_text()
    bridge_permission = (src / "permissions" / "reflex-desktop.toml").read_text()
    capability_permissions = json.loads((src / "capabilities" / "default.json").read_text())[
        "permissions"
    ]

    assert 'notify-rust = "4"' in cargo
    assert 'identifier = "reflex-desktop-notify"' in bridge_permission
    assert 'commands.allow = ["reflex_desktop_notify"]' in bridge_permission
    assert "reflex_desktop_terminal_log" not in bridge_permission
    assert "reflex-desktop-notify" in capability_permissions
    assert "fn reflex_desktop_run_notification_helper" in main_rs
    assert "fn reflex_desktop_notify" in main_rs
    # The bridge command is registered inside the managed command region.
    assert "// >>> reflex-desktop commands >>>" in main_rs
    assert "reflex_desktop_notify," in main_rs
    assert main_rs.count(".invoke_handler(") == 1
    assert "reflex_desktop_terminal_log" not in main_rs
    assert "reflex-desktop notify:" not in main_rs
    assert "notify helper:" not in main_rs
    assert "--reflex-desktop-notify-helper" in main_rs
    assert 'format!("{app_name} Notifications")' in main_rs
    assert ".appname(app_name)" in main_rs
    assert "desktop-entry" not in main_rs


def _build_remote(tmp_path, monkeypatch, **kwargs):
    """Scaffold a remote project with the given plugin kwargs and return src-tauri.

    Args:
        tmp_path: Pytest temp dir.
        monkeypatch: Pytest monkeypatch.
        kwargs: Extra DesktopPlugin params.

    Returns:
        The ``src-tauri`` path.
    """
    static_dir = tmp_path / ".web" / "build" / "client"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "index.html").write_text("x")
    monkeypatch.chdir(tmp_path)
    DesktopPlugin(
        backend="remote", product_name="App", identifier="dev.reflex.app", **kwargs
    ).post_build(static_dir=static_dir)
    return tmp_path / "tauri" / "src-tauri"


def test_window_options_and_global_tauri_applied(tmp_path, monkeypatch):
    """Extended window options + withGlobalTauri land in tauri.conf.json; unset ones omitted."""
    src = _build_remote(
        tmp_path,
        monkeypatch,
        resizable=True,
        min_width=900,
        min_height=650,
        decorations=False,
        theme="Dark",
    )
    conf = json.loads((src / "tauri.conf.json").read_text())
    assert conf["app"]["withGlobalTauri"] is True
    window = conf["app"]["windows"][0]
    assert window["resizable"] is True
    assert (window["minWidth"], window["minHeight"]) == (900, 650)
    assert window["decorations"] is False
    assert window["theme"] == "Dark"
    assert "fullscreen" not in window  # unset -> left to Tauri's default


def test_window_options_reapplied_on_rebuild(tmp_path, monkeypatch):
    """rxconfig is the source of truth: a changed window option applies on a later build."""
    _build_remote(tmp_path, monkeypatch, window_width=1100)
    src = _build_remote(tmp_path, monkeypatch, window_width=1400)
    conf = json.loads((src / "tauri.conf.json").read_text())
    assert conf["app"]["windows"][0]["width"] == 1400


def test_capabilities_include_bridge_and_plugin_perms(tmp_path, monkeypatch):
    """Capabilities grant core:default, app bridge, window perms, plugins, and extras."""
    src = _build_remote(
        tmp_path, monkeypatch, tauri_plugins=("notification",), extra_capabilities=("os:default",)
    )
    perms = json.loads((src / "capabilities" / "default.json").read_text())["permissions"]
    assert "core:default" in perms
    assert "reflex-desktop-notify" in perms
    assert "core:window:allow-minimize" in perms
    assert "notification:default" in perms
    assert "os:default" in perms


def test_no_bridge_perms_when_global_tauri_disabled(tmp_path, monkeypatch):
    """Disabling the bridge drops the window-control permissions and withGlobalTauri."""
    src = _build_remote(tmp_path, monkeypatch, with_global_tauri=False)
    perms = json.loads((src / "capabilities" / "default.json").read_text())["permissions"]
    assert "reflex-desktop-notify" not in perms
    assert "core:window:allow-minimize" not in perms
    conf = json.loads((src / "tauri.conf.json").read_text())
    assert conf["app"]["withGlobalTauri"] is False


def test_extra_plugins_injected_and_idempotent(tmp_path, monkeypatch):
    """Extra plugins inject into Cargo.toml + main.rs once, and rebuilds rewrite (not dup)."""
    src = _build_remote(tmp_path, monkeypatch, tauri_plugins=("notification", "dialog"))
    cargo = (src / "Cargo.toml").read_text()
    assert 'tauri-plugin-notification = "2"' in cargo
    assert 'tauri-plugin-dialog = "2"' in cargo
    main_rs = (src / "src" / "main.rs").read_text()
    assert ".plugin(tauri_plugin_notification::init())" in main_rs
    assert ".plugin(tauri_plugin_dialog::init())" in main_rs

    # rebuild with the same set -> region rewritten, not duplicated
    _build_remote(tmp_path, monkeypatch, tauri_plugins=("notification", "dialog"))
    assert (src / "Cargo.toml").read_text().count('tauri-plugin-notification = "2"') == 1

    # removing a plugin on a later build drops it from the managed region
    _build_remote(tmp_path, monkeypatch, tauri_plugins=())
    assert "tauri-plugin-notification" not in (src / "Cargo.toml").read_text()


def test_icon_copied_over_bundle_icons(tmp_path, monkeypatch):
    """A configured icon image is copied over every bundle icon file."""
    data = b"\x89PNG\r\n\x1a\nFAKEICON"
    (tmp_path / "logo.png").write_bytes(data)
    src = _build_remote(tmp_path, monkeypatch, icon="logo.png")
    for name in ("32x32.png", "128x128.png", "128x128@2x.png", "icon.png"):
        assert (src / "icons" / name).read_bytes() == data
