"""Tests for rebuild-safe registration of custom #[tauri::command] functions (no Cargo)."""

from __future__ import annotations

import os
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


def _scaffold(tmp_path: Path) -> tuple[DesktopPlugin, Path]:
    src_tauri = tmp_path / "src-tauri"
    shutil.copytree(SCAFFOLD, src_tauri)
    plugin = DesktopPlugin(backend="embedded")
    plugin._substitute_tokens(src_tauri, "demo_app")
    return plugin, src_tauri


def _scaffold_with_custom_command(tmp_path: Path) -> tuple[DesktopPlugin, Path]:
    plugin, src_tauri = _scaffold(tmp_path)
    main_rs = src_tauri / "src" / "main.rs"
    main_rs.write_text(main_rs.read_text().replace("fn main() {", _CUSTOM, 1))
    return plugin, src_tauri


def _handler_body(main_rs: Path) -> str:
    """The text of the managed command region inside generate_handler!."""
    text = main_rs.read_text()
    return re.search(
        r"// >>> reflex-desktop commands >>>(.*?)// <<< reflex-desktop commands <<<",
        text,
        re.DOTALL,
    ).group(1)


def test_custom_command_in_main_is_registered_alongside_the_bridge(tmp_path):
    plugin, src_tauri = _scaffold_with_custom_command(tmp_path)
    plugin._apply_notification_bridge(src_tauri)

    body = _handler_body(src_tauri / "src" / "main.rs")
    assert "reflex_desktop_notify," in body
    assert "read_note," in body


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
    once = main_rs.read_text()
    plugin._apply_notification_bridge(src_tauri)
    twice = main_rs.read_text()

    assert once == twice
    assert twice.count(".invoke_handler(") == 1
    assert _handler_body(main_rs).count("read_note,") == 1


def test_unchanged_main_rs_is_not_rewritten(tmp_path):
    """A no-op rebuild must not touch main.rs — an mtime bump forces a full cargo recompile."""
    plugin, src_tauri = _scaffold_with_custom_command(tmp_path)
    main_rs = src_tauri / "src" / "main.rs"

    plugin._apply_notification_bridge(src_tauri)
    os.utime(main_rs, (0, 0))
    plugin._apply_notification_bridge(src_tauri)

    assert main_rs.stat().st_mtime == 0


def test_command_in_another_module_is_not_auto_registered(tmp_path):
    """A command outside main.rs must not be registered unqualified (it would not compile)."""
    plugin, src_tauri = _scaffold(tmp_path)
    (src_tauri / "src" / "commands.rs").write_text(
        "#[tauri::command]\nfn open_db() -> String { String::new() }\n"
    )
    plugin._apply_notification_bridge(src_tauri)

    body = _handler_body(src_tauri / "src" / "main.rs")
    assert "open_db" not in body
    # ...and it's not granted an invoke permission either.
    assert "open_db" not in (src_tauri / "permissions" / "reflex-desktop.toml").read_text()


def test_user_added_command_outside_region_is_preserved(tmp_path):
    """Commands a user adds inside the macro but outside the managed region survive rebuilds."""
    plugin, src_tauri = _scaffold(tmp_path)
    main_rs = src_tauri / "src" / "main.rs"
    text = main_rs.read_text().replace(
        "            // <<< reflex-desktop commands <<<",
        "            // <<< reflex-desktop commands <<<\n            my_module::custom,",
        1,
    )
    main_rs.write_text(text)

    plugin._apply_notification_bridge(src_tauri)
    assert "my_module::custom," in main_rs.read_text()


def test_migrates_legacy_single_line_handler(tmp_path):
    """An older scaffold's single-line handler is upgraded to the managed region form."""
    plugin, src_tauri = _scaffold(tmp_path)
    main_rs = src_tauri / "src" / "main.rs"
    # Recreate the pre-region scaffold shape.
    legacy = re.sub(
        r"\.invoke_handler\(tauri::generate_handler!\[.*?\]\)",
        ".invoke_handler(tauri::generate_handler![reflex_desktop_notify])",
        main_rs.read_text(),
        count=1,
        flags=re.DOTALL,
    )
    main_rs.write_text(legacy)
    assert "// >>> reflex-desktop commands >>>" not in main_rs.read_text()

    plugin._apply_notification_bridge(src_tauri)
    text = main_rs.read_text()
    assert "// >>> reflex-desktop commands >>>" in text
    assert text.count(".invoke_handler(") == 1
    assert "reflex_desktop_notify," in _handler_body(main_rs)
