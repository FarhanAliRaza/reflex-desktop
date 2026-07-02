"""Shared defaults and small helpers for reflex-desktop."""

from __future__ import annotations

import re
import zlib

# Fixed loopback port the embedded backend binds to. env.json bakes this at build
# time (the SPA has no runtime channel to learn a dynamic port), so it must be stable.
DEFAULT_PORT = 8513

# Range the per-app default port is derived into (kept out of common dev-server
# territory and below Linux's ephemeral range, which starts at 32768).
PORT_RANGE = (17600, 18599)


def default_port(identifier: str) -> int:
    """Derive a stable per-app default port from the bundle identifier.

    The port is baked into ``env.json`` at build time, so it must be deterministic —
    but a single fixed default would make any two installed reflex-desktop apps collide
    on launch. Hashing the (unique) reverse-DNS identifier spreads apps across
    :data:`PORT_RANGE` while staying reproducible across builds.

    Args:
        identifier: The app's reverse-DNS bundle identifier.

    Returns:
        A port in :data:`PORT_RANGE`, stable for the given identifier.
    """
    base, top = PORT_RANGE
    return base + zlib.crc32(identifier.encode("utf-8")) % (top - base + 1)

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
