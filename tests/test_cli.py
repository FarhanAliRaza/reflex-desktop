"""Unit tests for the ``reflex-desktop`` CLI helpers (no Rust/Cargo required)."""

from __future__ import annotations

import os
from types import SimpleNamespace

from reflex_desktop import cli, desktop


def _call_script_code(spec) -> str:
    """Extract the JavaScript code from a ``rx.call_script`` event spec.

    Args:
        spec: The Reflex event spec returned by a desktop helper.

    Returns:
        The JavaScript string passed to ``rx.call_script``.
    """
    return spec.args[0][1]._var_value


def test_desktop_notify_prefers_reflex_desktop_bridge():
    """desktop.notify() uses the Reflex bridge before falling back to Tauri's plugin."""
    script = _call_script_code(desktop.notify("Hello", "GNOME"))
    assert "reflex_desktop_notify" in script
    assert 'title: "Hello"' in script
    assert 'body: "GNOME"' in script
    assert "sendNotification" in script
    assert "terminalLog" not in script
    assert "console.debug" not in script


def test_desnap_env_restores_vscode_snap_originals(monkeypatch):
    """A snap-rewritten var is restored to the original recorded in ``*_VSCODE_SNAP_ORIG``."""
    monkeypatch.setenv("GTK_PATH", "/snap/code/247/usr/lib/x86_64-linux-gnu/gtk-3.0")
    monkeypatch.setenv("GTK_PATH_VSCODE_SNAP_ORIG", "/usr/lib/x86_64-linux-gnu/gtk-3.0")
    env = cli._desnap_env()
    assert env["GTK_PATH"] == "/usr/lib/x86_64-linux-gnu/gtk-3.0"
    assert "GTK_PATH_VSCODE_SNAP_ORIG" not in env


def test_desnap_env_unsets_when_original_was_empty(monkeypatch):
    """An empty ``*_VSCODE_SNAP_ORIG`` means the var was unset before the snap; unset it again."""
    monkeypatch.setenv("GIO_MODULE_DIR", "/home/u/snap/code/common/.cache/gio-modules")
    monkeypatch.setenv("GIO_MODULE_DIR_VSCODE_SNAP_ORIG", "")
    env = cli._desnap_env()
    assert "GIO_MODULE_DIR" not in env
    assert "GIO_MODULE_DIR_VSCODE_SNAP_ORIG" not in env


def test_desnap_env_strips_snap_library_paths(monkeypatch):
    """Snap ``/snap/.../lib`` entries are removed so the native binary uses the system loader."""
    monkeypatch.setenv(
        "LD_LIBRARY_PATH",
        os.pathsep.join(["/usr/lib", "/snap/core20/current/lib/x86_64-linux-gnu", "/opt/lib"]),
    )
    env = cli._desnap_env()
    parts = env["LD_LIBRARY_PATH"].split(os.pathsep)
    assert "/snap/core20/current/lib/x86_64-linux-gnu" not in parts
    assert parts == ["/usr/lib", "/opt/lib"]


def test_desnap_env_unsets_when_only_snap_paths(monkeypatch):
    """A purely-snap ``LD_LIBRARY_PATH`` is dropped entirely rather than left empty."""
    monkeypatch.setenv("LD_LIBRARY_PATH", "/snap/core20/current/lib/x86_64-linux-gnu")
    assert "LD_LIBRARY_PATH" not in cli._desnap_env()


def test_desnap_env_drops_snap_ld_preload(monkeypatch):
    """A snap ``LD_PRELOAD`` is removed; a non-snap one is preserved."""
    monkeypatch.setenv("LD_PRELOAD", "/snap/core20/current/lib/libfoo.so")
    assert "LD_PRELOAD" not in cli._desnap_env()

    monkeypatch.setenv("LD_PRELOAD", "/usr/lib/libfoo.so")
    assert cli._desnap_env()["LD_PRELOAD"] == "/usr/lib/libfoo.so"


def test_desnap_env_noop_without_snap(monkeypatch):
    """A clean environment passes through unchanged."""
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    env = cli._desnap_env()
    assert "LD_LIBRARY_PATH" not in env
    assert "LD_PRELOAD" not in env


def test_wait_for_port_aborts_when_dev_server_dies():
    """_wait_for_port raises instead of spinning when `reflex run` exits early."""
    import click
    import pytest

    class Dead:
        returncode = 3

        def poll(self):
            return 3

    with pytest.raises(click.ClickException, match="exited with code 3"):
        cli._wait_for_port(65500, Dead(), timeout=5)


def test_wait_for_port_returns_once_listening():
    """_wait_for_port returns as soon as the dev server port accepts connections."""
    import socket

    class Alive:
        returncode = None

        def poll(self):
            return None

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        cli._wait_for_port(port, Alive(), timeout=10)


def test_resolve_dev_frontend_port_uses_reflex_default_when_config_unset(monkeypatch):
    """An unset Reflex frontend_port means use Reflex's default and allow auto-increment."""
    calls = []
    monkeypatch.setattr(cli, "_reflex_default_frontend_port", lambda: 3000)

    def handle_port(service_name, port, auto_increment):
        calls.append((service_name, port, auto_increment))
        return port

    assert cli._resolve_dev_frontend_port(SimpleNamespace(frontend_port=None), handle_port) == 3000
    assert calls == [("frontend", 3000, True)]


def test_resolve_dev_frontend_port_honors_configured_port():
    """A configured Reflex frontend_port is used exactly, without auto-increment."""
    calls = []

    def handle_port(service_name, port, auto_increment):
        calls.append((service_name, port, auto_increment))
        return port

    assert cli._resolve_dev_frontend_port(SimpleNamespace(frontend_port=3456), handle_port) == 3456
    assert calls == [("frontend", 3456, False)]


def test_resolve_dev_backend_port_uses_reflex_default_when_config_unset(monkeypatch):
    """An unset Reflex backend_port means use Reflex's default and allow auto-increment."""
    calls = []
    monkeypatch.setattr(cli, "_reflex_default_backend_port", lambda: 8000)

    def handle_port(service_name, port, auto_increment):
        calls.append((service_name, port, auto_increment))
        return port

    assert cli._resolve_dev_backend_port(SimpleNamespace(backend_port=None), handle_port) == 8000
    assert calls == [("backend", 8000, True)]


def test_resolve_dev_backend_port_honors_configured_port():
    """A configured Reflex backend_port is used exactly, without auto-increment."""
    calls = []

    def handle_port(service_name, port, auto_increment):
        calls.append((service_name, port, auto_increment))
        return port

    assert cli._resolve_dev_backend_port(SimpleNamespace(backend_port=8765), handle_port) == 8765
    assert calls == [("backend", 8765, False)]


def test_dev_backend_health_url_honors_backend_path():
    """The backend readiness check follows Reflex's backend_path prefix."""
    config = SimpleNamespace(prepend_backend_path=lambda path: f"/api{path}")
    assert cli._dev_backend_health_url(config, 8765) == "http://127.0.0.1:8765/api/_health"


def test_dev_passes_resolved_ports_and_backend_url_to_reflex(monkeypatch, tmp_path):
    """The dev command starts Reflex on the same concrete ports baked into the webview."""
    from click.testing import CliRunner

    from reflex_desktop.config import DEV_BACKEND_URL_ENV, VITE_FORCE_FULL_RELOAD_ENV
    from reflex_desktop.plugin import DesktopPlugin

    cwd = os.getcwd()
    plugin = DesktopPlugin(product_name="App", identifier="dev.reflex.app")
    src_tauri = tmp_path / "tauri-dev" / "src-tauri"
    src_tauri.mkdir(parents=True)

    monkeypatch.setattr(cli, "_preflight", lambda *, bundle: None)
    monkeypatch.setattr(cli, "_find_plugin", lambda app_root: plugin)
    monkeypatch.setattr(
        cli,
        "_dev_server_config",
        lambda: (3456, 8765, "http://127.0.0.1:8765", "http://127.0.0.1:8765/_health"),
    )
    monkeypatch.setattr(cli, "_reflex_cmd", lambda: ["reflex"])
    monkeypatch.setattr(DesktopPlugin, "_configure", lambda self, src_tauri: None)

    wait_ports = []
    monkeypatch.setattr(cli, "_wait_for_port", lambda port, process: wait_ports.append(port))
    health_urls = []
    monkeypatch.setattr(
        cli, "_wait_for_http_ok", lambda url, process: health_urls.append(url)
    )

    run_commands = []
    monkeypatch.setattr(
        cli, "_run", lambda cmd, cwd, env=None: run_commands.append((cmd, cwd, env))
    )

    popen_commands = []

    class FakeProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def popen(cmd, cwd, env=None):
        popen_commands.append((cmd, cwd, env))
        return FakeProcess()

    monkeypatch.setattr(cli.subprocess, "Popen", popen)

    try:
        result = CliRunner().invoke(cli.main, ["dev", "--app-dir", str(tmp_path)])
    finally:
        os.chdir(cwd)

    assert result.exit_code == 0, result.output
    assert len(popen_commands) == 1
    popen_cmd, popen_cwd, popen_env = popen_commands[0]
    assert popen_cmd == [
        "reflex",
        "run",
        "--frontend-port",
        "3456",
        "--backend-port",
        "8765",
    ]
    assert popen_cwd == tmp_path.resolve()
    assert popen_env[DEV_BACKEND_URL_ENV] == "http://127.0.0.1:8765"
    assert popen_env[VITE_FORCE_FULL_RELOAD_ENV] == "true"
    assert wait_ports == [3456]
    assert health_urls == ["http://127.0.0.1:8765/_health"]
    assert run_commands[0][0][:4] == ["cargo", "tauri", "dev", "--config"]
    assert "http://localhost:3456" in run_commands[0][0][4]
