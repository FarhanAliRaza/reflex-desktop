"""Unit tests for the ``reflex-desktop`` CLI helpers (no Rust/Cargo required)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from click.testing import CliRunner

from reflex_desktop import cli, desktop

SCAFFOLD = Path(__file__).resolve().parent.parent / "src/reflex_desktop/scaffold/embedded/src-tauri"


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


def test_codegen_writes_typed_bindings_for_custom_commands(tmp_path):
    """`reflex-desktop codegen` emits a typed wrapper for each #[tauri::command]."""
    src_tauri = tmp_path / "tauri" / "src-tauri"
    shutil.copytree(SCAFFOLD, src_tauri)
    main_rs = src_tauri / "src" / "main.rs"
    main_rs.write_text(
        main_rs.read_text().replace(
            "fn main() {",
            '#[tauri::command]\nfn add(a: i32, b: i32) -> i32 { a + b }\n\nfn main() {',
            1,
        )
    )

    result = CliRunner().invoke(cli.main, ["codegen", "--app-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output

    generated = (tmp_path / "desktop_commands.py").read_text()
    assert "def add(a: int, b: int, *, callback" in generated
    assert 'desktop.invoke("add", {"a": a, "b": b}, callback=callback)' in generated
    # Internal bridge command is excluded by default.
    assert "reflex_desktop_notify" not in generated
    compile(generated, "<generated>", "exec")


def test_codegen_errors_without_a_scaffold(tmp_path):
    """Codegen needs a built/scaffolded Tauri project."""
    result = CliRunner().invoke(cli.main, ["codegen", "--app-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "run `reflex-desktop build` first" in result.output


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
