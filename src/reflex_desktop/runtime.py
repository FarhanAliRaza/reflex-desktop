"""Assemble a relocatable Python runtime for the embedded (PyO3) backend.

Lays out three directories next to the Tauri crate that ``tauri.conf.json`` ships as
bundle resources:

- ``python/``        — a relocatable python-build-standalone interpreter.
- ``site-packages/`` — reflex + the user app + uvicorn, pip-installed for that interpreter.
- ``app/``           — the app payload (``rxconfig.py``, the app package, ``bootstrap.py``).

The Rust shell links PyO3 against the same interpreter (``PYO3_PYTHON``) at build time and
points ``PYTHONHOME``/``PYTHONPATH`` at ``python/`` and ``site-packages/`` at runtime.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

# A pinned python-build-standalone release. Override via assemble(release_tag=...).
DEFAULT_RELEASE_TAG = "20260623"
DEFAULT_PYTHON_VERSION = "3.12.13"


def host_triple() -> str:
    """Return the python-build-standalone target triple for the current host.

    Returns:
        The triple string used in release asset names.

    Raises:
        RuntimeError: If the host platform/arch is unsupported.
    """
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        machine
    )
    if arch is None:
        msg = f"reflex-desktop: unsupported architecture {machine!r}"
        raise RuntimeError(msg)

    system = sys.platform
    if system.startswith("linux"):
        return f"{arch}-unknown-linux-gnu"
    if system == "darwin":
        return f"{arch}-apple-darwin"
    if system in ("win32", "cygwin"):
        return f"{arch}-pc-windows-msvc"
    msg = f"reflex-desktop: unsupported platform {system!r}"
    raise RuntimeError(msg)


def standalone_url(python_version: str, release_tag: str, triple: str) -> str:
    """Build the download URL for a python-build-standalone ``install_only`` archive.

    Args:
        python_version: CPython version, e.g. ``"3.12.8"``.
        release_tag: python-build-standalone release tag, e.g. ``"20250115"``.
        triple: Target triple from :func:`host_triple`.

    Returns:
        The full download URL for the relocatable interpreter archive.
    """
    base = "https://github.com/astral-sh/python-build-standalone/releases/download"
    asset = f"cpython-{python_version}+{release_tag}-{triple}-install_only.tar.gz"
    return f"{base}/{release_tag}/{asset}"


def _interpreter_path(python_dir: Path) -> Path:
    """Return the interpreter executable inside an extracted standalone tree.

    Args:
        python_dir: The ``python/`` directory containing the extracted ``python/`` tree.

    Returns:
        Path to the platform-appropriate interpreter executable.
    """
    root = python_dir / "python"
    if sys.platform.startswith("win"):
        return root / "python.exe"
    return root / "bin" / "python3"


def fetch_runtime(python_dir: Path, python_version: str, release_tag: str) -> Path:
    """Download and extract a relocatable interpreter into ``python_dir`` (idempotent).

    Args:
        python_dir: Destination directory for the interpreter tree.
        python_version: CPython version to fetch.
        release_tag: python-build-standalone release tag.

    Returns:
        Path to the extracted interpreter executable.
    """
    interpreter = _interpreter_path(python_dir)
    if interpreter.exists():
        return interpreter

    url = standalone_url(python_version, release_tag, host_triple())
    python_dir.mkdir(parents=True, exist_ok=True)
    archive = python_dir / "runtime.tar.gz"
    print(f"reflex-desktop: downloading {url}")
    urllib.request.urlretrieve(url, archive)  # noqa: S310 - fixed GitHub release host
    with tarfile.open(archive) as tf:
        tf.extractall(python_dir)  # noqa: S202 - trusted release archive
    archive.unlink()
    return interpreter


def install_site_packages(
    interpreter: Path,
    site_packages: Path,
    requirements: list[str],
    requirements_file: Path | None = None,
) -> None:
    """Install the backend dependencies into ``site_packages`` for the bundled interpreter.

    Args:
        interpreter: The bundled interpreter executable.
        site_packages: Destination ``--target`` directory.
        requirements: pip requirement specifiers (reflex, uvicorn[standard], ...).
        requirements_file: Optional ``requirements.txt`` with the app's own dependencies.
    """
    extra = ["-r", str(requirements_file)] if requirements_file else []
    subprocess.run(
        [
            str(interpreter),
            "-m",
            "pip",
            "install",
            "--target",
            str(site_packages),
            *requirements,
            *extra,
        ],
        check=True,
    )


def _deps_stamp(requirements: list[str], requirements_file: Path | None) -> str:
    """Compute a marker describing the installed dependency set.

    Args:
        requirements: pip requirement specifiers.
        requirements_file: Optional app ``requirements.txt``.

    Returns:
        A stable text stamp; an unchanged stamp means a reinstall can be skipped.
    """
    parts = sorted(requirements)
    if requirements_file and requirements_file.exists():
        parts += ["--", requirements_file.read_text()]
    return "\n".join(parts)


def install_plugin_package(site_packages: Path) -> None:
    """Copy the ``reflex_desktop`` package into the bundle's site-packages.

    The app's ``rxconfig.py`` does ``from reflex_desktop import DesktopPlugin``, so the embedded
    backend (which evaluates ``rxconfig.py`` to read config) must be able to import it. It is
    a build-time plugin users don't declare as a runtime dependency, and may be an editable or
    unpublished checkout, so its source is copied rather than pip-installed. The build-only
    ``scaffold/`` templates are omitted — the backend never scaffolds.

    Args:
        site_packages: The bundle's ``site-packages`` directory.
    """
    pkg_src = Path(__file__).parent
    dest = site_packages / pkg_src.name
    if dest.exists():
        shutil.rmtree(dest)
    site_packages.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pkg_src, dest, ignore=shutil.ignore_patterns("__pycache__", "scaffold"))


def copy_app_payload(app_root: Path, dest: Path) -> None:
    """Copy ``rxconfig.py``, the app package and ``bootstrap.py`` into the bundle payload.

    Args:
        app_root: The app root holding ``rxconfig.py``.
        dest: Destination ``app/`` directory in the bundle.
    """
    from reflex_base.config import get_config

    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(app_root / "rxconfig.py", dest / "rxconfig.py")

    app_name = get_config().app_name
    pkg = app_root / app_name
    if pkg.is_dir():
        shutil.copytree(pkg, dest / app_name, dirs_exist_ok=True)

    shutil.copy2(Path(__file__).parent / "bootstrap.py", dest / "reflex_desktop_bootstrap.py")


def assemble(
    src_tauri: Path,
    app_root: Path,
    *,
    extra_requirements: list[str] | None = None,
    python_version: str = DEFAULT_PYTHON_VERSION,
    release_tag: str = DEFAULT_RELEASE_TAG,
) -> Path:
    """Assemble the full embedded runtime under ``src_tauri`` and return the interpreter.

    Args:
        src_tauri: The Tauri crate directory to populate with bundle resources.
        app_root: The Reflex app root.
        extra_requirements: Additional pip specifiers to install.
        python_version: CPython version to bundle.
        release_tag: python-build-standalone release tag.

    Returns:
        Path to the bundled interpreter (use as ``PYO3_PYTHON`` for ``cargo build``).
    """
    interpreter = fetch_runtime(src_tauri / "python", python_version, release_tag)
    requirements = ["reflex", "uvicorn[standard]", *(extra_requirements or [])]
    req_file = app_root / "requirements.txt"
    req_file = req_file if req_file.exists() else None

    site_packages = src_tauri / "site-packages"
    stamp = _deps_stamp(requirements, req_file)
    marker = site_packages / ".reflex_desktop_deps"
    if marker.exists() and marker.read_text() == stamp:
        print("reflex-desktop: embedded deps already installed (skipping pip).")
    else:
        install_site_packages(interpreter, site_packages, requirements, requirements_file=req_file)
        marker.write_text(stamp)

    # Always refresh the plugin package and app payload so edited code is picked up between
    # runs (and so a skipped pip install doesn't leave them stale).
    install_plugin_package(site_packages)
    copy_app_payload(app_root, src_tauri / "app")
    return interpreter


# Make the interpreter resolution reusable by callers that already fetched the runtime.
interpreter_path = _interpreter_path
