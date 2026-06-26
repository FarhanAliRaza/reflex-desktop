"""Shared defaults and small helpers for reflex-desktop."""

from __future__ import annotations

import re

# Fixed loopback port the embedded backend binds to. env.json bakes this at build
# time (the SPA has no runtime channel to learn a dynamic port), so it must be stable.
DEFAULT_PORT = 8513

# Default Tauri project directory (relative to the app root that holds rxconfig.py).
# Holds ``src-tauri/`` and the copied static frontend in ``dist/``.
DEFAULT_TAURI_DIR = "tauri"

# Loopback host. Deliberately 127.0.0.1, not "localhost": the compiled state.js rewrites
# a "localhost" backend host to window.location.hostname (under Tauri that is
# "tauri.localhost"), which would break the connection. 127.0.0.1 is left untouched.
LOOPBACK_HOST = "127.0.0.1"


def slugify(name: str) -> str:
    """Turn a product name into a valid lowercase Cargo crate / binary name.

    Args:
        name: The human-readable product name.

    Returns:
        A lowercase, hyphen-separated slug safe for Cargo.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "reflex-app"
