"""Unit tests for the embedded-backend helpers (no Rust/PyO3/network required)."""

from __future__ import annotations

import os
import sys

import pytest

from reflex_desktop import DesktopPlugin, bootstrap, runtime


def test_bootstrap_prepare_sets_env_and_path(tmp_path, monkeypatch):
    """prepare() chdir's into the app root, puts it on sys.path, and sets Reflex env."""
    from reflex_base.environment import environment

    (tmp_path / "rxconfig.py").write_text("config = None\n")
    monkeypatch.delenv("REFLEX_ENV_MODE", raising=False)
    environment.REFLEX_SKIP_COMPILE.set(None)

    try:
        port = bootstrap.prepare(tmp_path, 8513)

        assert port == 8513
        assert str(tmp_path.resolve()) in sys.path
        # REFLEX_SKIP_COMPILE is internal (env-ignored): it must be set via the API, not os.environ.
        assert environment.REFLEX_SKIP_COMPILE.get() is True
        assert os.environ.get("REFLEX_ENV_MODE") == "prod"
    finally:
        environment.REFLEX_SKIP_COMPILE.set(None)


def test_bootstrap_prepare_requires_rxconfig(tmp_path):
    """prepare() fails loudly when the app root has no rxconfig.py."""
    with pytest.raises(FileNotFoundError):
        bootstrap.prepare(tmp_path, 8513)


def test_standalone_url_shape():
    """The download URL points at an astral python-build-standalone install_only asset."""
    url = runtime.standalone_url("3.12.8", "20250115", "x86_64-unknown-linux-gnu")
    assert url == (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        "20250115/cpython-3.12.8+20250115-x86_64-unknown-linux-gnu-install_only.tar.gz"
    )


@pytest.mark.parametrize(
    ("plat", "machine", "expected"),
    [
        ("linux", "x86_64", "x86_64-unknown-linux-gnu"),
        ("darwin", "arm64", "aarch64-apple-darwin"),
        ("win32", "AMD64", "x86_64-pc-windows-msvc"),
    ],
)
def test_host_triple(monkeypatch, plat, machine, expected):
    """host_triple maps platform + arch to the python-build-standalone triple."""
    monkeypatch.setattr(runtime.sys, "platform", plat)
    monkeypatch.setattr(runtime.platform, "machine", lambda: machine)
    assert runtime.host_triple() == expected


def test_host_triple_rejects_unknown_arch(monkeypatch):
    """An unsupported architecture is an explicit error, not a malformed URL."""
    monkeypatch.setattr(runtime.platform, "machine", lambda: "mips")
    with pytest.raises(RuntimeError):
        runtime.host_triple()


def test_install_site_packages_passes_requirements_file(monkeypatch, tmp_path):
    """The app's requirements.txt is forwarded to pip as ``-r``."""
    calls = []
    monkeypatch.setattr(runtime.subprocess, "run", lambda cmd, check: calls.append(cmd))

    req = tmp_path / "requirements.txt"
    req.write_text("httpx\n")
    runtime.install_site_packages(
        tmp_path / "py", tmp_path / "site", ["reflex"], requirements_file=req
    )

    assert calls and "-r" in calls[0] and str(req) in calls[0]


def _stub_assemble(monkeypatch, tmp_path):
    """Stub out the network/pip/copy side effects of assemble; count pip installs.

    Returns:
        A list that accumulates one entry per ``install_site_packages`` call.
    """
    (tmp_path / "rxconfig.py").write_text("config = None\n")
    installs: list[object] = []
    monkeypatch.setattr(
        runtime, "fetch_runtime", lambda *a, **k: tmp_path / "py" / "bin" / "python3"
    )
    monkeypatch.setattr(runtime, "copy_app_payload", lambda *a, **k: None)

    def fake_install(interpreter, site_packages, requirements, requirements_file=None):
        site_packages.mkdir(parents=True, exist_ok=True)
        installs.append(requirements)

    monkeypatch.setattr(runtime, "install_site_packages", fake_install)
    return installs


def test_assemble_skips_pip_when_deps_unchanged(monkeypatch, tmp_path):
    """A second assemble with the same deps reuses the install (marker match -> no pip)."""
    installs = _stub_assemble(monkeypatch, tmp_path)
    src_tauri = tmp_path / "tauri" / "src-tauri"
    src_tauri.mkdir(parents=True)

    runtime.assemble(src_tauri, tmp_path)
    runtime.assemble(src_tauri, tmp_path)
    assert len(installs) == 1
    assert (src_tauri / "site-packages" / ".reflex_desktop_deps").exists()


def test_assemble_reinstalls_when_requirements_change(monkeypatch, tmp_path):
    """Changing the app's requirements.txt invalidates the marker and reinstalls."""
    installs = _stub_assemble(monkeypatch, tmp_path)
    src_tauri = tmp_path / "tauri" / "src-tauri"
    src_tauri.mkdir(parents=True)

    runtime.assemble(src_tauri, tmp_path)
    (tmp_path / "requirements.txt").write_text("httpx\n")
    runtime.assemble(src_tauri, tmp_path)
    assert len(installs) == 2


def _patch_config(monkeypatch, origins):
    """Make get_config() return a stub config with the given CORS origins."""
    from types import SimpleNamespace

    import reflex_base.config as rxconfig

    monkeypatch.setattr(
        rxconfig, "get_config", lambda: SimpleNamespace(cors_allowed_origins=origins)
    )


def _capture_warnings(monkeypatch):
    """Capture console.warn messages into a list."""
    import reflex_base.utils.console as console

    warnings: list[str] = []
    monkeypatch.setattr(console, "warn", lambda msg, **kw: warnings.append(msg))
    return warnings


def test_cors_warning_when_embedded_origins_restricted(monkeypatch):
    """Embedded mode warns when the Tauri origin is not allowed."""
    _patch_config(monkeypatch, ["https://example.com"])
    warnings = _capture_warnings(monkeypatch)
    DesktopPlugin(backend="embedded")._warn_if_cors_blocks()
    assert warnings and "cors_allowed_origins" in warnings[0]


def test_no_cors_warning_when_wildcard(monkeypatch):
    """A wildcard origin satisfies the Tauri webview; no warning."""
    _patch_config(monkeypatch, ["*"])
    warnings = _capture_warnings(monkeypatch)
    DesktopPlugin(backend="embedded")._warn_if_cors_blocks()
    assert not warnings


def test_no_cors_warning_for_remote(monkeypatch):
    """Remote mode's CORS lives on the remote server, so the local check is skipped."""
    _patch_config(monkeypatch, ["https://example.com"])
    warnings = _capture_warnings(monkeypatch)
    DesktopPlugin(backend="remote", backend_url="https://example.com")._warn_if_cors_blocks()
    assert not warnings
