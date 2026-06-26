"""Preflight checks for the native toolchain ``reflex-desktop`` needs to build a Tauri app.

The desktop build shells out to ``cargo`` (and, for ``--bundle``, the Tauri CLI) and links
against the platform WebView. A missing Rust toolchain or WebView dev package otherwise
surfaces only as a cryptic mid-compile linker / ``pkg-config`` error. These checks run before
the build (and via ``reflex-desktop doctor``) to turn that into actionable, copy-pasteable
guidance.

This module is intentionally free of ``click`` and side effects: it returns :class:`Check`
results and lets the CLI decide how to present them and whether to abort.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import sys

TAURI_PREREQS_URL = "https://tauri.app/start/prerequisites/"

_RUSTUP_INSTALL = (
    "Install the Rust toolchain (rustup):\n"
    "  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh\n"
    "  (Windows: download and run rustup-init.exe from https://rustup.rs)\n"
    "then restart your shell so `cargo` is on PATH."
)

_TAURI_CLI_INSTALL = (
    "Install the Tauri CLI (needed for --bundle):\n"
    "  cargo install tauri-cli --locked\n"
    "  (or a prebuilt binary:  cargo binstall tauri-cli)"
)

_LINUX_DEPS = (
    "Install Tauri's Linux WebView/build dependencies, e.g.:\n"
    "  Debian/Ubuntu:  sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \\\n"
    "                    libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev\n"
    "  Fedora:         sudo dnf install webkit2gtk4.1-devel openssl-devel curl wget file \\\n"
    "                    libappindicator-gtk3-devel librsvg2-devel\n"
    '                  sudo dnf group install "c-development"\n'
    "  Arch:           sudo pacman -S --needed webkit2gtk-4.1 base-devel curl wget file "
    "openssl \\\n"
    "                    appmenu-gtk-module libappindicator-gtk3 librsvg\n"
    f"See {TAURI_PREREQS_URL} for other distros."
)


@dataclasses.dataclass(frozen=True)
class Check:
    """Result of a single preflight check.

    Attributes:
        name: Short human-readable name of the dependency checked.
        ok: Whether the dependency is present / satisfied.
        detail: What was found (or not), shown next to the result.
        remediation: How to fix it when not ``ok`` (empty when ``ok``).
        required: Whether a failure should block the build.
    """

    name: str
    ok: bool
    detail: str
    remediation: str = ""
    required: bool = True


def _cargo_version() -> str | None:
    """Return the installed ``cargo`` version string, or ``None`` if cargo is unavailable.

    Returns:
        The first line of ``cargo --version``, or ``None`` when cargo is not on PATH.
    """
    if not shutil.which("cargo"):
        return None
    try:
        result = subprocess.run(["cargo", "--version"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    return result.stdout.strip() or "installed (version unknown)"


def _pkg_config_has(package: str) -> bool | None:
    """Report whether ``pkg-config`` knows about a package.

    Args:
        package: The pkg-config package name (e.g. ``"webkit2gtk-4.1"``).

    Returns:
        ``True`` / ``False`` if pkg-config could answer, or ``None`` when pkg-config itself
        is not installed (so the answer is unknown).
    """
    if not shutil.which("pkg-config"):
        return None
    try:
        result = subprocess.run(["pkg-config", "--exists", package], check=False)
    except OSError:
        return None
    return result.returncode == 0


def check_cargo() -> Check:
    """Check that the Rust toolchain (``cargo``) is installed.

    Returns:
        A :class:`Check` for the Rust toolchain.
    """
    version = _cargo_version()
    if version:
        return Check("Rust toolchain (cargo)", True, version)
    return Check("Rust toolchain (cargo)", False, "`cargo` not found on PATH", _RUSTUP_INSTALL)


def check_tauri_cli(*, required: bool) -> Check:
    """Check that the Tauri CLI (``cargo-tauri``) is installed.

    Args:
        required: Whether a failure should block the build (true for ``--bundle``).

    Returns:
        A :class:`Check` for the Tauri CLI.
    """
    name = "Tauri CLI (cargo-tauri)"
    suffix = "" if required else " (only needed for --bundle)"
    if not shutil.which("cargo"):
        return Check(name, False, f"requires cargo, which is missing{suffix}", "", required)
    try:
        result = subprocess.run(
            ["cargo", "tauri", "--version"], capture_output=True, text=True, check=False
        )
    except OSError:
        result = None
    if result is not None and result.returncode == 0:
        return Check(name, True, (result.stdout.strip() or "installed") + suffix, required=required)
    return Check(name, False, f"not installed{suffix}", _TAURI_CLI_INSTALL, required=required)


def check_platform_webview() -> list[Check]:
    """Check the platform-specific WebView / build dependencies Tauri links against.

    Returns:
        A list of platform-appropriate :class:`Check` results (empty on unknown platforms).
    """
    if sys.platform.startswith("linux"):
        name = "Linux WebView deps (webkit2gtk-4.1)"
        has = _pkg_config_has("webkit2gtk-4.1")
        if has is True:
            return [Check(name, True, "webkit2gtk-4.1 found")]
        detail = (
            "pkg-config not found (cannot verify WebView deps)"
            if has is None
            else "webkit2gtk-4.1 not found"
        )
        return [Check(name, False, detail, _LINUX_DEPS)]
    if sys.platform == "darwin":
        name = "Xcode Command Line Tools"
        try:
            ok = (
                subprocess.run(["xcode-select", "-p"], capture_output=True, check=False).returncode
                == 0
            )
        except OSError:
            ok = False
        if ok:
            return [Check(name, True, "installed")]
        return [Check(name, False, "not found", "Install them with:  xcode-select --install")]
    if sys.platform in ("win32", "cygwin"):
        # MSVC + WebView2 can't be reliably auto-detected here (MSVC is only on PATH inside a
        # developer prompt; WebView2 needs a registry probe), so surface guidance, don't block.
        return [
            Check(
                "Windows build deps (MSVC + WebView2)",
                True,
                "ensure the Microsoft C++ Build Tools (MSVC) and the WebView2 runtime are "
                "installed (WebView2 ships with Windows 11 / recent Windows 10) — "
                f"{TAURI_PREREQS_URL}",
                required=False,
            )
        ]
    return []


def run_checks(*, bundle: bool = False) -> list[Check]:
    """Run all build prerequisite checks for the current platform.

    Args:
        bundle: Whether the Tauri CLI is also required (the ``--bundle`` path).

    Returns:
        The ordered list of :class:`Check` results.
    """
    return [check_cargo(), *check_platform_webview(), check_tauri_cli(required=bundle)]


def failed_required(checks: list[Check]) -> list[Check]:
    """Return the required checks that did not pass.

    Args:
        checks: Checks produced by :func:`run_checks`.

    Returns:
        The subset of ``checks`` that are required and not ``ok``.
    """
    return [c for c in checks if c.required and not c.ok]
