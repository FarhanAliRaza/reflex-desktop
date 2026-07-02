"""Unit tests for the ``reflex_desktop.desktop`` native-API helpers (no Tauri required)."""

from __future__ import annotations

import reflex as rx

from reflex_desktop import desktop


def _call_script_code(spec) -> str:
    """Extract the JavaScript code from a ``rx.call_script`` event spec.

    Args:
        spec: The Reflex event spec returned by a desktop helper.

    Returns:
        The JavaScript string passed to ``rx.call_script``.
    """
    return spec.args[0][1]._var_value


def test_invoke_converts_arg_names_to_camel_case():
    """invoke() sends Tauri's default camelCase wire names for snake_case Python args."""
    script = _call_script_code(desktop.invoke("save_note", {"file_path": "/tmp/x", "count": 2}))
    assert 'window.__TAURI__.core.invoke("save_note"' in script
    assert '"filePath": "/tmp/x"' in script
    assert '"count": 2' in script
    assert "file_path" not in script


def test_invoke_keeps_snake_case_when_disabled():
    """convert_keys=False preserves arg spellings for rename_all = "snake_case" commands."""
    script = _call_script_code(
        desktop.invoke("save_note", {"file_path": "/tmp/x"}, convert_keys=False)
    )
    assert '"file_path": "/tmp/x"' in script
    assert "filePath" not in script


def test_invoke_without_args_sends_empty_payload():
    """invoke() with no args still passes a payload object."""
    script = _call_script_code(desktop.invoke("ping"))
    assert script == 'window.__TAURI__.core.invoke("ping", {})'


def test_open_file_builds_dialog_options():
    """open_file() drives the dialog plugin with converted filter objects."""

    script = _call_script_code(
        desktop.open_file(
            rx.console_log,
            multiple=True,
            title="Pick images",
            filters={"Images": ["png", "jpg"]},
        )
    )
    assert "window.__TAURI__.dialog.open(" in script
    assert '"multiple": true' in script
    assert '"directory": false' in script
    assert '"title": "Pick images"' in script
    assert '{"name": "Images", "extensions": ["png", "jpg"]}' in script


def test_save_file_builds_dialog_options():
    """save_file() passes defaultPath and filters to the dialog plugin."""

    script = _call_script_code(
        desktop.save_file(rx.console_log, default_path="report.csv", filters={"CSV": ["csv"]})
    )
    assert "window.__TAURI__.dialog.save(" in script
    assert '"defaultPath": "report.csv"' in script
    assert '{"name": "CSV", "extensions": ["csv"]}' in script


def test_clipboard_helpers():
    """Clipboard helpers use the clipboard-manager plugin's global API."""

    write = _call_script_code(desktop.clipboard_write('he said "hi"'))
    assert write == 'window.__TAURI__.clipboardManager.writeText("he said \\"hi\\"")'
    read = _call_script_code(desktop.clipboard_read(rx.console_log))
    assert read == "window.__TAURI__.clipboardManager.readText()"
