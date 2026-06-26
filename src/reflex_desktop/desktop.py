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
"""

from __future__ import annotations

import json

import reflex as rx

#: The current Tauri window object in the webview (Tauri 2 global API).
_WINDOW = "window.__TAURI__.window.getCurrentWindow()"


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
