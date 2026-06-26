"""Package a Reflex app as a native desktop app with Tauri."""

from . import desktop
from .plugin import DesktopPlugin

__all__ = ["DesktopPlugin", "desktop"]
