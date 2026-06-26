"""Unit tests for ``reflex_desktop.preflight`` and the ``doctor`` CLI command."""

from __future__ import annotations

import subprocess

from click.testing import CliRunner

from reflex_desktop import cli, preflight


def _completed(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    """Build a fake ``CompletedProcess`` for a stubbed ``subprocess.run``.

    Args:
        returncode: The exit code to report.
        stdout: The captured stdout text.

    Returns:
        A ``CompletedProcess`` standing in for a real subprocess result.
    """
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _stub_tools(monkeypatch, *, which, run):
    """Replace the ``shutil.which`` / ``subprocess.run`` the preflight module calls.

    Args:
        monkeypatch: The pytest monkeypatch fixture.
        which: Callable replacing ``shutil.which`` (name -> path or None).
        run: Callable replacing ``subprocess.run`` (argv -> CompletedProcess).
    """
    monkeypatch.setattr(preflight.shutil, "which", which)
    monkeypatch.setattr(preflight.subprocess, "run", lambda cmd, *a, **k: run(cmd))


def test_check_cargo_present(monkeypatch):
    """A cargo on PATH is reported ok with its version string."""
    _stub_tools(
        monkeypatch,
        which=lambda name: "/usr/bin/cargo" if name == "cargo" else None,
        run=lambda cmd: _completed(0, "cargo 1.79.0 (abc 2024-01-01)"),
    )
    check = preflight.check_cargo()
    assert check.ok
    assert "1.79.0" in check.detail
    assert check.remediation == ""


def test_check_cargo_absent(monkeypatch):
    """A missing cargo is a required failure with rustup install guidance."""
    _stub_tools(monkeypatch, which=lambda name: None, run=lambda cmd: _completed(127))
    check = preflight.check_cargo()
    assert not check.ok
    assert check.required
    assert "rustup" in check.remediation
    assert "sh.rustup.rs" in check.remediation


def test_check_tauri_cli_present(monkeypatch):
    """An installed cargo-tauri is reported ok."""
    _stub_tools(
        monkeypatch,
        which=lambda name: "/usr/bin/cargo",
        run=lambda cmd: _completed(0, "tauri-cli 2.0.0"),
    )
    check = preflight.check_tauri_cli(required=True)
    assert check.ok
    assert "2.0.0" in check.detail


def test_check_tauri_cli_missing_required(monkeypatch):
    """When --bundle is requested, a missing Tauri CLI is a required failure."""
    _stub_tools(
        monkeypatch,
        which=lambda name: "/usr/bin/cargo",
        run=lambda cmd: _completed(1),
    )
    check = preflight.check_tauri_cli(required=True)
    assert not check.ok
    assert check.required
    assert "cargo install tauri-cli" in check.remediation


def test_check_tauri_cli_missing_optional_does_not_block(monkeypatch):
    """Without --bundle, a missing Tauri CLI is optional and excluded from failures."""
    _stub_tools(
        monkeypatch,
        which=lambda name: "/usr/bin/cargo",
        run=lambda cmd: _completed(1),
    )
    check = preflight.check_tauri_cli(required=False)
    assert not check.ok
    assert not check.required
    assert preflight.failed_required([check]) == []


def test_check_tauri_cli_without_cargo(monkeypatch):
    """Without cargo, the Tauri CLI check fails without duplicating cargo's remediation."""
    _stub_tools(monkeypatch, which=lambda name: None, run=lambda cmd: _completed(0))
    check = preflight.check_tauri_cli(required=True)
    assert not check.ok
    assert "cargo" in check.detail


def test_linux_webview_present(monkeypatch):
    """A pkg-config that resolves webkit2gtk-4.1 passes the Linux check."""
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    _stub_tools(
        monkeypatch,
        which=lambda name: "/usr/bin/pkg-config",
        run=lambda cmd: _completed(0),
    )
    checks = preflight.check_platform_webview()
    assert len(checks) == 1
    assert checks[0].ok


def test_linux_webview_missing(monkeypatch):
    """A missing webkit2gtk-4.1 is a required failure with distro install commands."""
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    _stub_tools(
        monkeypatch,
        which=lambda name: "/usr/bin/pkg-config",
        run=lambda cmd: _completed(1),
    )
    check = preflight.check_platform_webview()[0]
    assert not check.ok
    assert check.required
    assert "webkit2gtk-4.1" in check.detail
    assert "apt install" in check.remediation


def test_linux_webview_without_pkgconfig(monkeypatch):
    """No pkg-config means the deps can't be verified; treat as a required failure."""
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    _stub_tools(monkeypatch, which=lambda name: None, run=lambda cmd: _completed(0))
    check = preflight.check_platform_webview()[0]
    assert not check.ok
    assert "pkg-config not found" in check.detail


def test_macos_xcode_present(monkeypatch):
    """Xcode CLT present (xcode-select -p succeeds) passes on macOS."""
    monkeypatch.setattr(preflight.sys, "platform", "darwin")
    _stub_tools(
        monkeypatch,
        which=lambda name: None,
        run=lambda cmd: _completed(0, "/Library/Developer/CommandLineTools"),
    )
    check = preflight.check_platform_webview()[0]
    assert check.ok


def test_macos_xcode_missing(monkeypatch):
    """Missing Xcode CLT is a required failure with the install command."""
    monkeypatch.setattr(preflight.sys, "platform", "darwin")
    _stub_tools(monkeypatch, which=lambda name: None, run=lambda cmd: _completed(2))
    check = preflight.check_platform_webview()[0]
    assert not check.ok
    assert "xcode-select --install" in check.remediation


def test_windows_is_informational(monkeypatch):
    """Windows can't auto-detect MSVC/WebView2, so the check is informational, never blocking."""
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    checks = preflight.check_platform_webview()
    assert len(checks) == 1
    assert checks[0].ok
    assert not checks[0].required
    assert preflight.failed_required(checks) == []


def test_run_checks_tauri_cli_required_only_with_bundle(monkeypatch):
    """The Tauri CLI check is required only when bundle=True."""
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    _stub_tools(
        monkeypatch,
        which=lambda name: "/usr/bin/" + name,
        run=lambda cmd: _completed(0, "v"),
    )
    by_name = {c.name: c for c in preflight.run_checks(bundle=False)}
    assert not by_name["Tauri CLI (cargo-tauri)"].required
    by_name = {c.name: c for c in preflight.run_checks(bundle=True)}
    assert by_name["Tauri CLI (cargo-tauri)"].required


def test_failed_required_filters():
    """failed_required returns only required, not-ok checks."""
    checks = [
        preflight.Check("a", ok=True, detail=""),
        preflight.Check("b", ok=False, detail="", required=True),
        preflight.Check("c", ok=False, detail="", required=False),
    ]
    assert [c.name for c in preflight.failed_required(checks)] == ["b"]


def test_doctor_passes_when_all_ok(monkeypatch):
    """`doctor` exits 0 and reports success when every required check passes."""
    monkeypatch.setattr(
        preflight,
        "run_checks",
        lambda *, bundle=False: [preflight.Check("Rust toolchain (cargo)", True, "cargo 1.79.0")],
    )
    result = CliRunner().invoke(cli.main, ["doctor"])
    assert result.exit_code == 0
    assert "All required checks passed" in result.output
    assert "[ok" in result.output or "ok]" in result.output


def test_doctor_fails_and_prints_remediation(monkeypatch):
    """`doctor` exits non-zero and prints remediation when a required check fails."""
    monkeypatch.setattr(
        preflight,
        "run_checks",
        lambda *, bundle=False: [
            preflight.Check("Rust toolchain (cargo)", False, "missing", "Install rustup: ...")
        ],
    )
    result = CliRunner().invoke(cli.main, ["doctor"])
    assert result.exit_code != 0
    assert "Install rustup" in result.output
    assert "MISSING" in result.output


def test_build_preflight_aborts_on_missing_toolchain(monkeypatch, tmp_path):
    """_build_app runs preflight first and aborts (no export/compile) when cargo is missing."""
    monkeypatch.chdir(tmp_path)
    called = {"export": False}
    monkeypatch.setattr(cli, "_reflex_export", lambda app_root: called.__setitem__("export", True))
    monkeypatch.setattr(
        preflight,
        "run_checks",
        lambda *, bundle=False: [
            preflight.Check("Rust toolchain (cargo)", False, "missing", "Install rustup")
        ],
    )
    runner = CliRunner()
    result = runner.invoke(cli.main, ["build", "--app-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "Install rustup" in result.output
    assert called["export"] is False
