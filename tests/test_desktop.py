"""Unit tests for the reflex_desktop.desktop native bridge (no Tauri/webview required)."""

from __future__ import annotations

import reflex as rx

from reflex_desktop import desktop


def _script(spec: rx.event.EventSpec) -> str:
    """Pull the JavaScript string out of a call_script EventSpec."""
    args = {var._js_expr: value for var, value in spec.args}
    return args["javascript_code"]._var_value


def test_invoke_no_args_passes_empty_object():
    """A no-argument command still gets an explicit empty args object."""
    script = _script(desktop.invoke("my_command"))
    assert script == 'window.__TAURI__.core.invoke("my_command", {})'


def test_invoke_serializes_args_as_json():
    """Args are JSON-encoded into the invoke payload."""
    script = _script(desktop.invoke("write_file", {"path": "/tmp/x", "contents": "hi"}))
    assert script == (
        'window.__TAURI__.core.invoke("write_file", '
        '{"path": "/tmp/x", "contents": "hi"})'
    )


def test_invoke_supports_plugin_command_syntax():
    """Plugin commands are addressed as plugin:<name>|<command>."""
    script = _script(desktop.invoke("plugin:fs|read_text_file", {"path": "/etc/hostname"}))
    assert 'window.__TAURI__.core.invoke("plugin:fs|read_text_file"' in script


def test_invoke_without_callback_has_null_callback():
    """Fire-and-forget invoke routes no result back."""
    spec = desktop.invoke("my_command")
    callback = {var._js_expr: value for var, value in spec.args}["callback"]
    assert callback._var_value is None


def test_invoke_with_callback_wires_the_handler():
    """A callback is attached so the command's return value reaches a Reflex handler."""

    class State(rx.State):
        @rx.event
        def on_result(self, value):
            pass

    spec = desktop.invoke("my_command", callback=State.on_result)
    callback = {var._js_expr: value for var, value in spec.args}["callback"]
    assert callback._var_value is not None
