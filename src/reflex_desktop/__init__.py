"""Package a Reflex app as a native desktop app with Tauri."""

from . import desktop as desktop
from .plugin import DesktopPlugin as DesktopPlugin

__all__ = ["DesktopPlugin", "desktop"]
