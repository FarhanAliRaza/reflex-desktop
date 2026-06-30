"""``reflex-desktop`` command-line entrypoint.

Orchestrates the desktop build: ``reflex export --frontend-only`` (which fires the
``DesktopPlugin`` to bake env.json, scaffold the Tauri project, and copy in the static
frontend), then compiles the Tauri shell with ``cargo``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from . import codegen, preflight
from .config import DEFAULT_TAURI_DIR, slugify


def _find_plugin(app_root: Path):
    """Find the ``DesktopPlugin`` configured in the app's ``rxconfig.py``.

    Args:
        app_root: The app root (holding ``rxconfig.py``).

    Returns:
        The configured ``DesktopPlugin`` instance, or ``None`` if unavailable.
    """
    try:
        from reflex_base.config import get_config

        from .plugin import DesktopPlugin

        for plugin in get_config().plugins:
            if isinstance(plugin, DesktopPlugin):
                return plugin
    except Exception as exc:  # noqa: BLE001 - config import is best-effort
        click.echo(f"reflex-desktop: could not read rxconfig ({exc})", err=True)
    return None


def _run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    """Run a subprocess, echoing the command and aborting on failure.

    Args:
        cmd: The command and arguments.
        cwd: Working directory.
        env: Optional environment override for the subprocess.

    Raises:
        ClickException: If the command exits non-zero or is not found.
    """
    click.echo(f"reflex-desktop: $ {' '.join(cmd)}  (in {cwd})")
    try:
        result = subprocess.run(cmd, cwd=cwd, check=False, env=env)
    except FileNotFoundError as exc:
        raise click.ClickException(f"command not found: {cmd[0]} ({exc})") from exc
    if result.returncode != 0:
        raise click.ClickException(f"{cmd[0]} exited with code {result.returncode}")


def _desnap_env() -> dict[str, str]:
    """Build a launch environment freed of snap-confinement leakage.

    When the CLI runs inside a snap (e.g. VS Code's integrated terminal, which is a snap),
    the snap rewrites ``GTK_PATH``/``GIO_MODULE_DIR``/``GTK_IM_MODULE_FILE``/``LOCPATH``/
    ``XDG_DATA_DIRS`` and friends to point at its own bundled runtime. A system-native binary
    that inherits them loads GTK/GIO/IM modules built against the snap's older glibc and dies
    at startup with ``undefined symbol: __libc_pthread_init, version GLIBC_PRIVATE``. The snap
    records each pre-snap value in ``<VAR>_VSCODE_SNAP_ORIG``; restoring those (empty original
    means the var was originally unset) hands the binary the system environment. Snap entries
    in ``LD_LIBRARY_PATH`` / ``LD_PRELOAD`` are scrubbed too, as a fallback for snaps that keep
    no such record.

    Returns:
        A copy of the current environment cleaned for launching a system-native binary.
    """
    env = dict(os.environ)
    suffix = "_VSCODE_SNAP_ORIG"
    for key in [k for k in env if k.endswith(suffix)]:
        original = env.pop(key)
        var = key[: -len(suffix)]
        if original:
            env[var] = original
        else:
            env.pop(var, None)
    ld = env.get("LD_LIBRARY_PATH")
    if ld:
        kept = [p for p in ld.split(os.pathsep) if p and "/snap/" not in p]
        if kept:
            env["LD_LIBRARY_PATH"] = os.pathsep.join(kept)
        else:
            env.pop("LD_LIBRARY_PATH", None)
    if "/snap/" in env.get("LD_PRELOAD", ""):
        env.pop("LD_PRELOAD", None)
    return env


def _reflex_export(app_root: Path) -> None:
    """Build the static frontend via ``reflex export --frontend-only``.

    Args:
        app_root: The app root to run the export in.

    Raises:
        ClickException: If the ``reflex`` executable cannot be located.
    """
    # Prefer the reflex executable from the same venv as the running interpreter, so the
    # build uses the env reflex-desktop is installed in regardless of PATH / the cwd's project.
    sibling = Path(sys.executable).parent / ("reflex.exe" if os.name == "nt" else "reflex")
    reflex_bin = str(sibling) if sibling.exists() else shutil.which("reflex")
    cmd = [reflex_bin] if reflex_bin else [sys.executable, "-m", "reflex"]
    _run([*cmd, "export", "--frontend-only"], cwd=app_root)


def _crate_name(src_tauri: Path) -> str | None:
    """Read the Cargo crate name from ``Cargo.toml`` (for reporting the binary path).

    Args:
        src_tauri: The ``src-tauri`` directory.

    Returns:
        The crate name, or ``None`` if it can't be parsed.
    """
    cargo = src_tauri / "Cargo.toml"
    if not cargo.exists():
        return None
    for line in cargo.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("name") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"')
    return None


def _install_desktop_entry(plugin, src_tauri: Path, binary: Path) -> None:
    """Install a dev ``.desktop`` entry so GNOME displays the app's notifications (Linux).

    GNOME's notification daemon resolves each notification's ``desktop-entry`` hint (Tauri
    sets it to the app identifier) to an installed ``.desktop`` file and silently drops the
    notification when none matches — which is why a dev binary's notifications never appear on
    GNOME even though ``notify-send`` works. Writing ``~/.local/share/applications/
    <identifier>.desktop`` keyed by the identifier makes GNOME accept them. No-op off Linux.

    Args:
        plugin: The configured ``DesktopPlugin`` (source of the product name / identifier).
        src_tauri: The ``src-tauri`` directory (for the icon path).
        binary: The built binary the entry points at.
    """
    if sys.platform != "linux" or plugin is None:
        return
    product_name, _identifier, _ = plugin._resolved_names()
    # GNOME resolves a notification to an app by its `app_name`, which Tauri sets to the
    # binary/crate name (confirmed over D-Bus) — NOT the bundle identifier. The .desktop file
    # must be named to match, or GNOME drops the notification.
    app_id = _crate_name(src_tauri) or slugify(product_name)
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    icon = src_tauri / "icons" / "128x128.png"
    entry = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={product_name}\n"
        f"Exec={binary}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        f"StartupWMClass={app_id}\n"
    )
    (apps_dir / f"{app_id}.desktop").write_text(entry)
    click.echo(
        f"reflex-desktop: installed dev desktop entry {app_id}.desktop "
        "(so GNOME shows this app's notifications)."
    )


def _preflight(*, bundle: bool) -> None:
    """Verify the native build toolchain is present, with actionable guidance if not.

    Runs before the (slow) export/compile so a missing Rust toolchain, WebView dev package,
    or Tauri CLI fails fast with copy-pasteable install steps instead of a cryptic linker
    error mid-build.

    Args:
        bundle: Whether the Tauri CLI (``cargo-tauri``) is also required (``--bundle``).

    Raises:
        ClickException: If a required dependency is missing, listing how to install each.
    """
    missing = preflight.failed_required(preflight.run_checks(bundle=bundle))
    if not missing:
        return
    lines = ["missing build prerequisites — install the following, then re-run:\n"]
    for check in missing:
        lines.append(f"  ✗ {check.name}: {check.detail}")
        lines += [f"      {line}" for line in check.remediation.splitlines()]
        lines.append("")
    lines.append("Run `reflex-desktop doctor` to recheck.")
    raise click.ClickException("\n".join(lines))


@click.group()
def main() -> None:
    """Build a Reflex app as a native desktop app with Tauri."""


@main.command()
@click.option(
    "--bundle",
    is_flag=True,
    default=False,
    help="Also check the Tauri CLI (cargo-tauri), required for --bundle.",
)
def doctor(bundle: bool) -> None:
    """Check the native toolchain needed to build a desktop app (Rust, WebView deps, Tauri CLI).

    Args:
        bundle: Also require the Tauri CLI (cargo-tauri), as ``build --bundle`` does.

    Raises:
        ClickException: If a required dependency is missing.
    """
    checks = preflight.run_checks(bundle=bundle)
    click.echo("reflex-desktop doctor — desktop build prerequisites\n")
    for check in checks:
        mark = click.style("ok", fg="green") if check.ok else click.style("MISSING", fg="red")
        click.echo(f"  [{mark}] {check.name}: {check.detail}")
        if not check.ok and check.remediation:
            click.echo("\n".join(f"         {line}" for line in check.remediation.splitlines()))
    click.echo(
        '\nEmbedded backend (backend="embedded") also fetches a Python runtime on first build '
        "(needs network)."
    )
    missing = preflight.failed_required(checks)
    if missing:
        raise click.ClickException(
            f"{len(missing)} required check(s) failed — install the above, then re-run."
        )
    click.echo(click.style("\nAll required checks passed.", fg="green"))


def _binary_path(src_tauri: Path, release: bool) -> Path | None:
    """Resolve the compiled binary path, honoring ``CARGO_TARGET_DIR``.

    Args:
        src_tauri: The ``src-tauri`` directory.
        release: Whether the release profile was built.

    Returns:
        Path to the binary, or ``None`` if the crate name can't be determined.
    """
    crate = _crate_name(src_tauri)
    if not crate:
        return None
    target = os.environ.get("CARGO_TARGET_DIR")
    target_dir = Path(target) if target else src_tauri / "target"
    name = f"{crate}.exe" if os.name == "nt" else crate
    return target_dir / ("release" if release else "debug") / name


def _build_app(app_dir: str, release: bool, bundle: bool, skip_export: bool):
    """Export the frontend and compile the Tauri binary.

    Args:
        app_dir: App root containing rxconfig.py.
        release: Build in release mode when true, debug otherwise.
        bundle: Use the Tauri CLI to produce installers.
        skip_export: Skip the reflex export step (reuse an existing build).

    Returns:
        A ``(src_tauri, plugin)`` tuple.

    Raises:
        ClickException: If the Tauri project was not produced by the export step.
    """
    app_root = Path(app_dir).resolve()
    # Run from the app root so config discovery, the export subprocess, and the plugin's
    # cwd-relative scaffolding all agree regardless of where this was launched.
    os.chdir(app_root)
    # Fail fast on a missing toolchain before the slow export + cargo compile.
    _preflight(bundle=bundle)
    plugin = _find_plugin(app_root)
    tauri_dir = plugin.tauri_dir if plugin else DEFAULT_TAURI_DIR

    if not skip_export:
        _reflex_export(app_root)

    src_tauri = app_root / tauri_dir / "src-tauri"
    if not src_tauri.is_dir():
        raise click.ClickException(
            f"no Tauri project at {src_tauri}. Did the export run with DesktopPlugin in "
            "rxconfig plugins?"
        )

    env = None
    if plugin is not None and plugin.backend == "embedded":
        from . import runtime

        click.echo("reflex-desktop: assembling embedded Python runtime (this may take a while)...")
        interpreter = runtime.assemble(src_tauri, app_root)
        env = {**os.environ, "PYO3_PYTHON": str(interpreter)}

    if bundle:
        if plugin is not None and plugin.backend == "embedded":
            click.echo(
                "reflex-desktop: note — bundling an embedded app is experimental; the bundled "
                "interpreter's libpython is found via an absolute rpath, so a relocated/installed "
                "bundle may fail to launch until the $ORIGIN-relative rpath work (M2) lands.",
                err=True,
            )
        cmd = ["cargo", "tauri", "build"] + ([] if release else ["--debug"])
    else:
        cmd = ["cargo", "build"] + (["--release"] if release else [])
    _run(cmd, cwd=src_tauri, env=env)
    return src_tauri, plugin


@main.command()
@click.option("--app-dir", default=".", help="App root containing rxconfig.py.")
@click.option("--release/--debug", default=True, help="Build profile (default: release).")
@click.option(
    "--bundle",
    is_flag=True,
    default=False,
    help="Produce installers via the Tauri CLI (cargo tauri build) instead of a bare binary.",
)
@click.option("--skip-export", is_flag=True, default=False, help="Skip the reflex export step.")
def build(app_dir: str, release: bool, bundle: bool, skip_export: bool) -> None:
    """Export the frontend and compile the Tauri desktop binary.

    Args:
        app_dir: App root containing rxconfig.py.
        release: Build in release mode when true, debug otherwise.
        bundle: Use the Tauri CLI to produce installers.
        skip_export: Skip the reflex export step (reuse an existing build).
    """
    src_tauri, _ = _build_app(app_dir, release, bundle, skip_export)
    binary = _binary_path(src_tauri, release)
    if binary:
        click.echo(f"reflex-desktop: built {binary}")


@main.command()
@click.option("--app-dir", default=".", help="App root containing rxconfig.py.")
@click.option(
    "--release/--debug",
    default=False,
    help="Build/launch profile (default: debug, for fast iteration; use --release to ship).",
)
@click.option("--skip-export", is_flag=True, default=False, help="Skip the reflex export step.")
@click.option(
    "--skip-build",
    is_flag=True,
    default=False,
    help="Launch the existing binary without rebuilding.",
)
def run(app_dir: str, release: bool, skip_export: bool, skip_build: bool) -> None:
    """Build the desktop app (unless ``--skip-build``) and launch it.

    Defaults to a debug build for fast iteration, mirroring ``cargo run``; pass ``--release``
    for an optimized launch (what ``reflex-desktop build`` produces for shipping).

    Args:
        app_dir: App root containing rxconfig.py.
        release: Build/launch the release profile when true, debug otherwise.
        skip_export: Skip the reflex export step (reuse an existing frontend build).
        skip_build: Launch the already-built binary without rebuilding.

    Raises:
        ClickException: If the binary can't be located.
    """
    if skip_build:
        app_root = Path(app_dir).resolve()
        os.chdir(app_root)
        plugin = _find_plugin(app_root)
        tauri_dir = plugin.tauri_dir if plugin else DEFAULT_TAURI_DIR
        src_tauri = app_root / tauri_dir / "src-tauri"
    else:
        src_tauri, plugin = _build_app(app_dir, release, bundle=False, skip_export=skip_export)

    binary = _binary_path(src_tauri, release)
    if binary is None or not binary.exists():
        raise click.ClickException(
            f"binary not found at {binary}; run `reflex-desktop build` first."
        )

    launch_env = _desnap_env()
    if plugin is not None and plugin.backend == "remote":
        target = plugin.backend_url or "config.api_url"
        click.echo(f"reflex-desktop: remote mode — ensure the backend is running at {target}.")
    elif plugin is not None and plugin.backend == "embedded":
        # The in-place dev binary can't find its resources via Tauri's resource_dir() (that
        # only resolves inside a real bundle), so point it at the assembled crate dir holding
        # python/, site-packages/ and app/.
        launch_env["REFLEX_DESKTOP_RESOURCE_DIR"] = str(src_tauri)

    _install_desktop_entry(plugin, src_tauri, binary)
    click.echo(f"reflex-desktop: launching {binary}")
    _run([str(binary)], cwd=src_tauri.parent.parent, env=launch_env)


@main.command("codegen")
@click.option("--app-dir", default=".", help="App root containing rxconfig.py.")
@click.option(
    "--out",
    default=None,
    help="Output path for the bindings module (default: <app_pkg>/desktop_commands.py).",
)
@click.option(
    "--include-internal",
    is_flag=True,
    default=False,
    help="Also emit reflex-desktop's own bridge commands (reflex_desktop_*).",
)
def codegen_cmd(app_dir: str, out: str | None, include_internal: bool) -> None:
    """Generate typed Python bindings from the app's #[tauri::command] definitions.

    Scans ``<tauri_dir>/src-tauri/src`` and writes a module of typed wrappers so commands
    can be called as ``desktop_commands.my_command(arg=...)`` instead of a stringly-typed
    ``desktop.invoke("my_command", {...})``. Run it after adding or changing a command.

    Args:
        app_dir: App root containing rxconfig.py.
        out: Where to write the generated module; defaults to the app package.
        include_internal: Include reflex-desktop's own bridge commands.

    Raises:
        ClickException: If no Tauri project has been scaffolded yet.
    """
    app_root = Path(app_dir).resolve()
    os.chdir(app_root)
    plugin = _find_plugin(app_root)
    tauri_dir = plugin.tauri_dir if plugin else DEFAULT_TAURI_DIR
    src_tauri = app_root / tauri_dir / "src-tauri"
    if not src_tauri.is_dir():
        raise click.ClickException(
            f"no Tauri project at {src_tauri}; run `reflex-desktop build` first."
        )

    commands = codegen.discover_commands(src_tauri)
    module = codegen.render_module(commands, include_internal=include_internal)
    if out:
        out_path = (app_root / out).resolve()
    else:
        try:
            from reflex_base.config import get_config

            app_name = getattr(get_config(), "app_name", None)
        except Exception:  # noqa: BLE001 - best-effort config read
            app_name = None
        out_path = codegen.default_output_path(app_root, app_name)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(module)
    shown = [
        c for c in commands if include_internal or not c.name.startswith("reflex_desktop_")
    ]
    click.echo(f"reflex-desktop: wrote {len(shown)} command binding(s) to {out_path}")


@main.command()
def dev() -> None:
    """Run the app in dev mode (not implemented in v1).

    Raises:
        ClickException: Always; dev/HMR mode is deferred past v1.
    """
    raise click.ClickException("`reflex-desktop dev` is not implemented yet (v1 is build-only).")


if __name__ == "__main__":
    main()
