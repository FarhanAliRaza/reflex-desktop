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

import importlib.metadata
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

# Stdlib directories never needed by a headless embedded backend (nor by the pip run that
# assembles it), pruned from the bundled runtime to cut installer size.
_STDLIB_PRUNE = ("test", "idlelib", "tkinter", "turtledemo")


def default_requirements() -> list[str]:
    """Return the base pip specifiers for the embedded backend, pinned to the build env.

    The static frontend is compiled by the reflex version installed in the developer's
    environment; the embedded backend must run the *same* version or the baked frontend
    and the backend protocol can drift (installing whatever PyPI serves on build day).
    Each specifier is pinned to the locally installed version when one can be resolved.

    Returns:
        Pip requirement specifiers for reflex and uvicorn.
    """
    requirements = []
    for package, spec in (("reflex", "reflex"), ("uvicorn", "uvicorn[standard]")):
        try:
            requirements.append(f"{spec}=={importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            requirements.append(spec)
    return requirements


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
    trim_runtime(python_dir)
    if sys.platform == "darwin":
        _rewrite_macos_libpython_id(python_dir)
    return interpreter


def _stdlib_dir(python_dir: Path) -> Path | None:
    """Locate the bundled stdlib directory (``lib/pythonX.Y`` or ``Lib`` on Windows).

    Args:
        python_dir: The ``python/`` directory containing the extracted tree.

    Returns:
        The stdlib directory, or ``None`` if the layout is unrecognized.
    """
    root = python_dir / "python"
    if not root.is_dir():
        return None
    # Match directory names exactly (via iterdir) rather than probing paths: on a
    # case-insensitive filesystem (macOS, Windows) a probe for the Windows-layout "Lib"
    # would match a unix-layout "lib" and return the wrong level.
    entries = {entry.name: entry for entry in root.iterdir() if entry.is_dir()}
    if "Lib" in entries:
        return entries["Lib"]
    lib = entries.get("lib")
    if lib is not None:
        for entry in sorted(lib.iterdir()):
            if entry.is_dir() and entry.name.startswith("python"):
                return entry
    return None


def trim_runtime(python_dir: Path) -> None:
    """Prune runtime pieces a headless embedded backend never uses (idempotent).

    Drops the stdlib test suite, IDLE, and tkinter/turtledemo plus the Tcl/Tk support
    trees — together well over 100 MB uncompressed — while keeping pip (still needed to
    assemble ``site-packages``).

    Args:
        python_dir: The ``python/`` directory containing the extracted tree.
    """
    stdlib = _stdlib_dir(python_dir)
    if stdlib is not None:
        for name in _STDLIB_PRUNE:
            shutil.rmtree(stdlib / name, ignore_errors=True)

    root = python_dir / "python"
    # Tcl/Tk data trees: lib/tcl8.6, lib/tk8.6, lib/itcl* on unix; tcl/ on Windows.
    lib = root / "lib"
    if lib.is_dir():
        for entry in lib.iterdir():
            if entry.is_dir() and entry.name.startswith(("tcl", "tk", "itcl", "thread")):
                shutil.rmtree(entry, ignore_errors=True)
    shutil.rmtree(root / "tcl", ignore_errors=True)
    shutil.rmtree(root / "share", ignore_errors=True)


def _rewrite_macos_libpython_id(python_dir: Path) -> None:
    """Give the bundled libpython an ``@rpath`` install name (macOS relocatability).

    PyO3 links the shell against this dylib, and the load command recorded in the app
    binary is whatever install name the dylib carries at link time. An absolute or
    ``@executable_path``-relative name only resolves on the build machine / next to the
    interpreter, so rewrite it to ``@rpath/libpythonX.Y.dylib`` — the shell's build script
    then supplies rpath entries for both the dev layout and the installed ``.app`` bundle.
    The dylib is re-signed ad-hoc because ``install_name_tool`` invalidates its signature.

    Args:
        python_dir: The ``python/`` directory containing the extracted tree.
    """
    lib = python_dir / "python" / "lib"
    if not lib.is_dir():
        return
    for dylib in lib.glob("libpython3.*.dylib"):
        if dylib.is_symlink():
            continue
        subprocess.run(
            ["install_name_tool", "-id", f"@rpath/{dylib.name}", str(dylib)],
            check=True,
        )
        if shutil.which("codesign"):
            subprocess.run(["codesign", "--force", "--sign", "-", str(dylib)], check=False)


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
    trim_site_packages(site_packages)


def trim_site_packages(site_packages: Path) -> None:
    """Strip install byproducts the bundled backend never reads (idempotent).

    Removes ``__pycache__`` trees (the runtime sets ``PYTHONDONTWRITEBYTECODE``) and the
    console-script shims pip drops into ``bin``/``Scripts`` under ``--target`` — their
    shebangs point at the build machine's interpreter and are never executed.

    Args:
        site_packages: The bundle's ``site-packages`` directory.
    """
    for cache in site_packages.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for scripts in ("bin", "Scripts"):
        shutil.rmtree(site_packages / scripts, ignore_errors=True)


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
        shutil.copytree(
            pkg,
            dest / app_name,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )

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
    requirements = [*default_requirements(), *(extra_requirements or [])]
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
