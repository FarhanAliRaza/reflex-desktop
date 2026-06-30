"""Tests for rebuild-safe registration of custom #[tauri::command] functions (no Cargo)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from reflex_desktop import DesktopPlugin

SCAFFOLD = Path(__file__).resolve().parent.parent / "src/reflex_desktop/scaffold/embedded/src-tauri"

_CUSTOM = """#[tauri::command(rename_all = "snake_case")]
fn read_note(file_path: String, max_len: Option<usize>) -> Result<String, String> {
    Ok(String::new())
}

fn main() {"""


def _scaffold_with_custom_command(tmp_path: Path) -> tuple[DesktopPlugin, Path]:
    src_tauri = tmp_path / "src-tauri"
    shutil.copytree(SCAFFOLD, src_tauri)
    plugin = DesktopPlugin(backend="embedded")
    plugin._substitute_tokens(src_tauri, "demo_app")
    main_rs = src_tauri / "src" / "main.rs"
    main_rs.write_text(main_rs.read_text().replace("fn main() {", _CUSTOM, 1))
    return plugin, src_tauri


def _handler(main_rs: Path) -> str:
    return re.search(
        r"\.invoke_handler\(tauri::generate_handler!\[[^\]]*\]\)", main_rs.read_text()
    ).group(0)


def test_custom_command_is_registered_alongside_the_bridge(tmp_path):
    plugin, src_tauri = _scaffold_with_custom_command(tmp_path)
    plugin._apply_notification_bridge(src_tauri)

    handler = _handler(src_tauri / "src" / "main.rs")
    assert "reflex_desktop_notify" in handler
    assert "read_note" in handler


def test_custom_command_gets_an_invoke_permission(tmp_path):
    plugin, src_tauri = _scaffold_with_custom_command(tmp_path)
    plugin._apply_notification_bridge(src_tauri)

    toml = (src_tauri / "permissions" / "reflex-desktop.toml").read_text()
    assert '"read_note"' in toml
    assert '"reflex_desktop_notify"' in toml


def test_registration_is_idempotent_across_rebuilds(tmp_path):
    """Re-running the build doesn't duplicate the handler or the command."""
    plugin, src_tauri = _scaffold_with_custom_command(tmp_path)
    main_rs = src_tauri / "src" / "main.rs"

    plugin._apply_notification_bridge(src_tauri)
    handler_once = _handler(main_rs)
    plugin._apply_notification_bridge(src_tauri)
    handler_twice = _handler(main_rs)

    assert handler_once == handler_twice
    assert main_rs.read_text().count(".invoke_handler(") == 1
    assert handler_twice.count("read_note") == 1
