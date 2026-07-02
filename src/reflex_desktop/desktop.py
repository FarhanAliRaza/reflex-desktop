"""Call native Tauri APIs from Reflex event handlers.

Each function returns a Reflex event (via ``rx.call_script``) that runs in the webview, so
use them directly as handlers::

    import reflex as rx
    from reflex_desktop import desktop

    rx.button("minimize", on_click=desktop.minimize())
    rx.button("close", on_click=desktop.close())

Requires ``DesktopPlugin(with_global_tauri=True)`` (the default), which exposes
``window.__TAURI__`` in the webview and grants the window-control permissions. On Linux,
``notify`` uses the Reflex notification bridge from the generated scaffold; elsewhere it
falls back to Tauri's notification plugin.

Beyond window controls, this module offers:

- :func:`invoke` — call any custom ``#[tauri::command]`` (with correct wire-name casing).
- :func:`open_file` / :func:`save_file` — native file dialogs (``tauri_plugins=("dialog",)``).
- :func:`clipboard_write` / :func:`clipboard_read` — system clipboard
  (``tauri_plugins=("clipboard-manager",)``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import reflex as rx

#: The current Tauri window object in the webview (Tauri 2 global API).
_WINDOW = "window.__TAURI__.window.getCurrentWindow()"


def _camel(name: str) -> str:
    """Convert a snake_case identifier to camelCase.

    Args:
        name: The snake_case name.

    Returns:
        The camelCase spelling.
    """
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def invoke(
    command: str,
    args: Mapping[str, Any] | None = None,
    *,
    callback: Any = None,
    convert_keys: bool = True,
) -> rx.event.EventSpec:
    """Invoke a Tauri command (a ``#[tauri::command]`` in ``main.rs``) from an event handler.

    By default the top-level argument names are converted from snake_case to camelCase,
    because that is how Tauri expects command arguments on the wire: a Rust command
    ``fn save(file_path: String)`` receives its argument as ``filePath`` unless it opts
    into ``#[tauri::command(rename_all = "snake_case")]``. Sending the Python spelling
    unconverted fails silently at runtime, so conversion is on by default; pass
    ``convert_keys=False`` for commands declared with ``rename_all = "snake_case"``.

    Args:
        command: The Tauri command name (e.g. ``"reflex_desktop_notify"``).
        args: JSON-serializable arguments for the command.
        callback: Optional Reflex event handler receiving the command's return value.
        convert_keys: Convert top-level arg names to camelCase (Tauri's default wire names).

    Returns:
        A Reflex event that invokes the command in the webview.
    """
    payload = {
        (_camel(key) if convert_keys else key): value for key, value in (args or {}).items()
    }
    script = f"window.__TAURI__.core.invoke({json.dumps(command)}, {json.dumps(payload)})"
    return rx.call_script(script, callback=callback)


def minimize() -> rx.event.EventSpec:
    """Minimize the window.

    Returns:
        A Reflex event that minimizes the window.
    """
    return rx.call_script(f"{_WINDOW}.minimize()")


def maximize() -> rx.event.EventSpec:
    """Maximize the window.

    Returns:
        A Reflex event that maximizes the window.
    """
    return rx.call_script(f"{_WINDOW}.maximize()")


def unmaximize() -> rx.event.EventSpec:
    """Restore the window from a maximized state.

    Returns:
        A Reflex event that unmaximizes the window.
    """
    return rx.call_script(f"{_WINDOW}.unmaximize()")


def toggle_maximize() -> rx.event.EventSpec:
    """Toggle the window between maximized and restored.

    Returns:
        A Reflex event that toggles maximization.
    """
    return rx.call_script(f"{_WINDOW}.toggleMaximize()")


def close() -> rx.event.EventSpec:
    """Close the window (quits a single-window app).

    Returns:
        A Reflex event that closes the window.
    """
    return rx.call_script(f"{_WINDOW}.close()")


def start_dragging() -> rx.event.EventSpec:
    """Begin dragging the window (use on ``on_mouse_down`` of a custom title bar).

    Returns:
        A Reflex event that starts a window drag.
    """
    return rx.call_script(f"{_WINDOW}.startDragging()")


def set_fullscreen(value: bool = True) -> rx.event.EventSpec:
    """Enter or leave fullscreen.

    Args:
        value: ``True`` to enter fullscreen, ``False`` to leave it.

    Returns:
        A Reflex event that sets the fullscreen state.
    """
    return rx.call_script(f"{_WINDOW}.setFullscreen({str(value).lower()})")


def set_title(title: str) -> rx.event.EventSpec:
    """Set the window title.

    Args:
        title: The new window title.

    Returns:
        A Reflex event that sets the window title.
    """
    return rx.call_script(f"{_WINDOW}.setTitle({json.dumps(title)})")


def _dialog_filters(filters: Mapping[str, Sequence[str]] | None) -> list[dict[str, Any]]:
    """Convert ``{"Images": ["png", "jpg"]}`` into Tauri dialog filter objects.

    Args:
        filters: Mapping of filter display name to allowed extensions (no leading dot).

    Returns:
        The dialog plugin's filter list (empty when ``filters`` is ``None``).
    """
    if not filters:
        return []
    return [{"name": name, "extensions": list(exts)} for name, exts in filters.items()]


def open_file(
    callback: Any,
    *,
    multiple: bool = False,
    directory: bool = False,
    title: str | None = None,
    filters: Mapping[str, Sequence[str]] | None = None,
) -> rx.event.EventSpec:
    """Show a native open-file (or folder) picker and send the selection to a handler.

    Requires ``DesktopPlugin(tauri_plugins=("dialog", ...))``. The callback receives the
    selected path as a string (a list of strings with ``multiple=True``), or ``None``
    when the user cancels.

    Args:
        callback: Reflex event handler receiving the selected path(s).
        multiple: Allow selecting more than one file.
        directory: Pick a directory instead of a file.
        title: Dialog title (platform default when ``None``).
        filters: Mapping of filter name to allowed extensions, e.g. ``{"Images": ["png"]}``.

    Returns:
        A Reflex event that opens the dialog.
    """
    options: dict[str, Any] = {"multiple": multiple, "directory": directory}
    if title is not None:
        options["title"] = title
    if filters:
        options["filters"] = _dialog_filters(filters)
    script = f"window.__TAURI__.dialog.open({json.dumps(options)})"
    return rx.call_script(script, callback=callback)


def save_file(
    callback: Any,
    *,
    default_path: str | None = None,
    title: str | None = None,
    filters: Mapping[str, Sequence[str]] | None = None,
) -> rx.event.EventSpec:
    """Show a native save-file picker and send the chosen path to a handler.

    Requires ``DesktopPlugin(tauri_plugins=("dialog", ...))``. The callback receives the
    chosen path as a string, or ``None`` when the user cancels. The dialog only picks the
    path — write the file from the event handler (the backend runs locally).

    Args:
        callback: Reflex event handler receiving the chosen path.
        default_path: Initial directory or suggested file name.
        title: Dialog title (platform default when ``None``).
        filters: Mapping of filter name to allowed extensions, e.g. ``{"CSV": ["csv"]}``.

    Returns:
        A Reflex event that opens the dialog.
    """
    options: dict[str, Any] = {}
    if default_path is not None:
        options["defaultPath"] = default_path
    if title is not None:
        options["title"] = title
    if filters:
        options["filters"] = _dialog_filters(filters)
    script = f"window.__TAURI__.dialog.save({json.dumps(options)})"
    return rx.call_script(script, callback=callback)


def clipboard_write(text: str) -> rx.event.EventSpec:
    """Write text to the system clipboard.

    Requires ``DesktopPlugin(tauri_plugins=("clipboard-manager", ...))``.

    Args:
        text: The text to place on the clipboard.

    Returns:
        A Reflex event that writes the clipboard.
    """
    return rx.call_script(f"window.__TAURI__.clipboardManager.writeText({json.dumps(text)})")


def clipboard_read(callback: Any) -> rx.event.EventSpec:
    """Read the system clipboard's text and send it to a handler.

    Requires ``DesktopPlugin(tauri_plugins=("clipboard-manager", ...))``.

    Args:
        callback: Reflex event handler receiving the clipboard text.

    Returns:
        A Reflex event that reads the clipboard.
    """
    return rx.call_script("window.__TAURI__.clipboardManager.readText()", callback=callback)


def notify(title: str, body: str = "") -> rx.event.EventSpec:
    """Show a native OS notification.

    On Linux the generated Reflex bridge sends with the product name as the application
    name. GNOME closes focused-app notifications when they resolve to the binary/desktop
    identity, so avoiding that association makes button-triggered notifications visible.
    If the bridge is unavailable, this falls back to Tauri's notification plugin and requests
    permission first when required.

    Args:
        title: Notification title.
        body: Notification body text.

    Returns:
        A Reflex event that requests permission (if needed) and sends a native notification.
    """
    payload = f"{{title: {json.dumps(title)}, body: {json.dumps(body)}}}"
    script = (
        "(async () => {"
        f"  const payload = {payload};"
        "  const tauri = window.__TAURI__;"
        "  const invoke = tauri && tauri.core && tauri.core.invoke;"
        "  if (invoke) {"
        "    try {"
        "      await invoke('reflex_desktop_notify', payload);"
        "      return;"
        "    }"
        "    catch (err) {}"
        "  }"
        "  const n = tauri && tauri.notification;"
        "  if (!n) {"
        "    console.error('reflex-desktop: notification plugin not available "
        '(add tauri_plugins=("notification",))\');'
        "    return;"
        "  }"
        "  let granted = await n.isPermissionGranted();"
        "  if (!granted) {"
        "    const permission = await n.requestPermission();"
        "    granted = permission === 'granted';"
        "  }"
        "  if (granted) {"
        "    await n.sendNotification(payload);"
        "  }"
        "})()"
    )
    return rx.call_script(script)
