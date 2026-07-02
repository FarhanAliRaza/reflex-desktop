"""Unit tests for the ``reflex-desktop`` CLI helpers (no Rust/Cargo required)."""

from __future__ import annotations

import os

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
