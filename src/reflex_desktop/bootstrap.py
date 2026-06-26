"""Embedded backend entrypoint, invoked by the Tauri Rust host via PyO3.

The Tauri shell (embedded mode) initializes a bundled CPython, then calls
``reflex_desktop_bootstrap.main()`` on a background thread. We run the Reflex ASGI app
in-process with uvicorn, bound to ``127.0.0.1:<port>``. The frontend is prebuilt and
served by Tauri, so this is a *write-free, backend-only* startup: ``REFLEX_SKIP_COMPILE``
makes ``App.__call__`` skip artifact generation and just register states/routes/handlers.

This deliberately bypasses ``reflex run`` / ``reflex.utils.exec.run_backend`` — that path
touches ``.web/nocompile`` and may select granian (a process-spawning server hostile to
in-process embedding). uvicorn driven as a library on one thread is what embedding needs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Env var the Rust host sets to the writable app root (holds ``rxconfig.py`` + app pkg).
APP_ROOT_ENV = "REFLEX_DESKTOP_APP_ROOT"
#: Env var the Rust host sets to the loopback port to bind.
PORT_ENV = "REFLEX_DESKTOP_PORT"
DEFAULT_PORT = 8513


def prepare(app_root: str | os.PathLike[str], port: int) -> int:
    """Configure cwd, ``sys.path`` and Reflex env for a write-free backend startup.

    Args:
        app_root: Writable directory holding ``rxconfig.py`` and the app package.
        port: Loopback port the backend will bind to.

    Returns:
        The resolved port.

    Raises:
        FileNotFoundError: If ``app_root`` has no ``rxconfig.py``.
    """
    root = Path(app_root).resolve()
    if not (root / "rxconfig.py").exists():
        msg = f"reflex_desktop_bootstrap: no rxconfig.py under {root}"
        raise FileNotFoundError(msg)

    os.chdir(root)
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    # Write-free, production backend-only startup (no frontend artifacts written). Unlike a
    # normal env var, REFLEX_SKIP_COMPILE is declared internal=True and is NOT read from the
    # OS environment — it must be set through the environment API, exactly as `reflex run`
    # does. Skipping this makes App.__call__ run a full frontend compile (bun install, etc.).
    from reflex_base.environment import environment

    environment.REFLEX_SKIP_COMPILE.set(True)
    os.environ.setdefault("REFLEX_ENV_MODE", "prod")
    # Keep generated state under the writable app root.
    os.environ.setdefault("REFLEX_WEB_WORKDIR", str(root / ".web"))
    return port


def main() -> None:
    """Resolve config from env and serve the Reflex ASGI app with uvicorn (blocking)."""
    app_root = os.environ.get(APP_ROOT_ENV, os.getcwd())
    port = int(os.environ.get(PORT_ENV, str(DEFAULT_PORT)))
    prepare(app_root, port)

    import uvicorn
    from reflex.utils.exec import get_app_instance

    # get_app_instance() -> "<app_module>:app"; factory=True calls App.__call__ to build
    # the ASGI app. Bind 127.0.0.1 (matches the baked env.json; off the network).
    uvicorn.run(
        get_app_instance(),
        factory=True,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        workers=1,
        reload=False,
    )


if __name__ == "__main__":
    main()
