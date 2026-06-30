"""Generate typed Python bindings from the Rust ``#[tauri::command]`` definitions.

The Rust source under ``src-tauri/src`` is the single source of truth: every
``#[tauri::command]`` function is parsed into a :class:`Command` (name, arguments, return
type), which feeds two things:

* the plugin's ``generate_handler![...]`` registration (see ``plugin.py``), so a command is
  wired up by virtue of existing in the source — no hand-maintained list, rebuild-safe; and
* a generated Python module of typed wrappers (``reflex-desktop codegen``), so calling a
  command from a Reflex event handler is statically checked and autocompleted instead of a
  stringly-typed ``desktop.invoke("name", {...})``.

The parser is deliberately lightweight (regex + a brace/paren scanner), not a full Rust
parser. It handles the shapes Tauri commands actually take — ``async``/``pub`` functions,
multi-line argument lists, generics, references, ``Option``/``Result``/``Vec``/map types —
and falls back to ``Any`` for anything it doesn't recognize rather than guessing wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Argument types Tauri injects itself (the AppHandle, the window, managed state, …). They
#: are not passed from JS, so they're dropped from the generated signature. Matched against
#: the type with references/lifetimes/generics stripped to its final path segment.
_INJECTED_TYPES = frozenset(
    {
        "AppHandle",
        "Window",
        "WebviewWindow",
        "Webview",
        "State",
        "Request",
        "Channel",
        "Connection",
    }
)

#: Rust scalar/std types mapped to their Python equivalents.
_SCALAR_TYPES = {
    "String": "str",
    "str": "str",
    "char": "str",
    "PathBuf": "str",
    "Path": "str",
    "OsString": "str",
    "bool": "bool",
    "i8": "int",
    "i16": "int",
    "i32": "int",
    "i64": "int",
    "i128": "int",
    "isize": "int",
    "u8": "int",
    "u16": "int",
    "u32": "int",
    "u64": "int",
    "u128": "int",
    "usize": "int",
    "f32": "float",
    "f64": "float",
}


#: Prefix marking reflex-desktop's own bridge commands (excluded from generated bindings).
_INTERNAL_PREFIX = "reflex_desktop_"

#: Default filename for the generated bindings module.
DEFAULT_STUB_FILENAME = "desktop_commands.py"

# Compiled once: a Tauri command attribute, the function header that follows it, the tokens
# that terminate a return type, and the leading qualifiers stripped off an argument type.
_CMD_ATTR_RE = re.compile(r"#\[\s*tauri::command\b")
_FN_RE = re.compile(r"\bfn\s+(\w+)\s*\(")
_RETURN_STOP_RE = re.compile(r"\{|;|\bwhere\b")
_LEADING_REF_RE = re.compile(r"^&\s*")
_LEADING_LIFETIME_RE = re.compile(r"^'\w+\s+")
_LEADING_MUT_RE = re.compile(r"^mut\s+")


def default_output_path(app_root: Path, app_name: str | None) -> Path:
    """Where bindings are written by default: inside the app package if it exists.

    Args:
        app_root: The app root (holds ``rxconfig.py``).
        app_name: The Reflex ``app_name`` (its package directory), if known.

    Returns:
        ``<app_root>/<app_name>/desktop_commands.py`` when that package exists, else
        ``<app_root>/desktop_commands.py``.
    """
    pkg = app_root / app_name if app_name else None
    base = pkg if pkg and pkg.is_dir() else app_root
    return base / DEFAULT_STUB_FILENAME


def resolve_output_path(app_root: Path, override: str | None = None) -> Path:
    """Resolve where the generated bindings module should be written.

    Shared by the CLI (``--out``) and the build-time plugin so both pick the same location.

    Args:
        app_root: The app root (holds ``rxconfig.py``).
        override: An explicit path relative to ``app_root``; when given it wins outright.

    Returns:
        The resolved output path.
    """
    if override:
        return (app_root / override).resolve()
    app_name = None
    try:
        from reflex_base.config import get_config

        config = get_config()
        app_name = getattr(config, "app_name", None) if config else None
    except Exception:  # noqa: BLE001 - config read is best-effort; fall back to app root
        app_name = None
    return default_output_path(app_root, app_name)


@dataclass(frozen=True)
class Argument:
    """A single command argument exposed to the Python side."""

    name: str
    python_type: str


@dataclass(frozen=True)
class Command:
    """A parsed ``#[tauri::command]`` function."""

    name: str
    arguments: tuple[Argument, ...] = ()
    return_type: str = "None"


def rust_type_to_python(rust: str) -> str:
    """Map a Rust type to the closest Python annotation.

    Unrecognized types fall back to ``Any`` so generated code stays valid.

    Args:
        rust: A Rust type as written in source (may include references, lifetimes, generics).

    Returns:
        A Python type annotation string.
    """
    t = rust.strip()
    # Strip references, mutability and leading lifetimes: `&'a mut str` -> `str`.
    t = _LEADING_REF_RE.sub("", t)
    t = _LEADING_LIFETIME_RE.sub("", t)
    t = _LEADING_MUT_RE.sub("", t)
    t = t.strip()

    if t in ("", "()"):
        return "None"

    outer, inner = _split_generic(t)
    base = outer.rsplit("::", 1)[-1]  # `std::string::String` -> `String`

    if inner is None:
        return _SCALAR_TYPES.get(base, "Any" if base not in _SCALAR_TYPES else base)

    args = _split_top_level(inner)
    if base == "Option" and len(args) == 1:
        return f"{rust_type_to_python(args[0])} | None"
    if base == "Result" and args:
        # The resolved value is the Ok type; the Err type surfaces as a rejected promise.
        return rust_type_to_python(args[0])
    if base == "Vec" and len(args) == 1:
        return f"list[{rust_type_to_python(args[0])}]"
    if base in ("HashMap", "BTreeMap") and len(args) == 2:
        return f"dict[{rust_type_to_python(args[0])}, {rust_type_to_python(args[1])}]"
    if base in ("Box", "Arc", "Rc") and len(args) == 1:
        return rust_type_to_python(args[0])
    return "Any"


def _split_generic(t: str) -> tuple[str, str | None]:
    """Split ``Vec<String>`` into ``("Vec", "String")``; no generics -> ``(t, None)``."""
    start = t.find("<")
    if start == -1 or not t.endswith(">"):
        return t, None
    return t[:start], t[start + 1 : -1]


def _split_top_level(s: str) -> list[str]:
    """Split on commas not nested inside ``<>`` or ``()``."""
    parts: list[str] = []
    depth = 0
    current = ""
    for ch in s:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def _is_injected(rust_type: str) -> bool:
    """Whether an argument type is something Tauri injects (not passed from JS)."""
    t = _LEADING_REF_RE.sub("", rust_type.strip())
    t = _LEADING_LIFETIME_RE.sub("", t).strip()
    base = _split_generic(t)[0].rsplit("::", 1)[-1].strip()
    return base in _INJECTED_TYPES


def scan_balanced(text: str, open_idx: int, open_ch: str, close_ch: str) -> int:
    """Return the index just past the balanced closer for the bracket at ``open_idx``.

    Args:
        text: The text to scan.
        open_idx: Index of the opening bracket.
        open_ch: The opening bracket character.
        close_ch: The matching closing bracket character.

    Returns:
        The index just past the matching closer, or ``-1`` if unbalanced.
    """
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _skip_double_quoted(text: str, i: int) -> int:
    """Given ``i`` at an opening ``"``, return the index just past the closing quote."""
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return len(text)


def _scan_attribute_end(text: str, open_idx: int) -> int:
    """Return the index past the ``]`` closing the attribute whose ``[`` is at ``open_idx``.

    Tracks nested brackets and skips ``"…"`` string literals so a ``]`` inside an attribute
    argument (e.g. ``#[tauri::command(msg = "oops]")]``) doesn't end the scan early.

    Args:
        text: The Rust source.
        open_idx: Index of the attribute's opening ``[``.

    Returns:
        The index just past the closing ``]``, or ``-1`` if unbalanced.
    """
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i = _skip_double_quoted(text, i)
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _parse_arguments(params: str) -> tuple[Argument, ...]:
    """Parse a parameter list, dropping ``self`` and Tauri-injected arguments."""
    args: list[Argument] = []
    for raw in _split_top_level(params):
        param = raw.strip()
        if not param or param.startswith("self") or param.startswith("&self"):
            continue
        if ":" not in param:
            continue
        name, _, rust_type = param.partition(":")
        name = name.strip().removeprefix("mut ").strip()
        rust_type = rust_type.strip()
        if name == "_" or _is_injected(rust_type):
            continue
        args.append(Argument(name=name, python_type=rust_type_to_python(rust_type)))
    return tuple(args)


def extract_commands(rust_source: str) -> list[Command]:
    """Parse every ``#[tauri::command]`` function out of a Rust source string.

    Args:
        rust_source: Contents of a ``.rs`` file.

    Returns:
        The parsed commands, in source order.
    """
    commands: list[Command] = []
    for attr in _CMD_ATTR_RE.finditer(rust_source):
        bracket = rust_source.find("[", attr.start())
        attr_end = _scan_attribute_end(rust_source, bracket)
        if attr_end == -1:
            continue
        # The command attribute binds to the next function (any further #[…]/#[cfg] lines and
        # the async/pub/const qualifiers sit between the attribute and `fn`).
        m = _FN_RE.search(rust_source, attr_end)
        if not m:
            continue
        name = m.group(1)
        paren_open = m.end() - 1
        paren_close = scan_balanced(rust_source, paren_open, "(", ")")
        if paren_close == -1:
            continue
        params = rust_source[paren_open + 1 : paren_close - 1]

        # Return type is between `->` and the body `{` — stopping at a `where` clause or a
        # `;` (trait/extern decl) so neither leaks into the type.
        rest = rust_source[paren_close:]
        stop = _RETURN_STOP_RE.search(rest)
        head = rest[: stop.start()] if stop else rest
        ret_match = re.search(r"->\s*(.+)", head, re.DOTALL)
        return_type = rust_type_to_python(ret_match.group(1).strip()) if ret_match else "None"

        commands.append(
            Command(name=name, arguments=_parse_arguments(params), return_type=return_type)
        )
    return commands


def discover_commands(src_tauri: Path) -> list[Command]:
    """Find the Tauri commands defined in ``src-tauri/src/main.rs``, sorted by name.

    Only ``main.rs`` is scanned: those are the commands the plugin registers in
    ``generate_handler!`` (where they must be in scope unqualified). Commands a user splits
    into other modules and registers by hand are intentionally not picked up here, so the
    generated bindings never expose a command that isn't actually wired up.

    Args:
        src_tauri: The ``src-tauri`` directory.

    Returns:
        Parsed commands, de-duplicated and sorted by name.
    """
    main_rs = src_tauri / "src" / "main.rs"
    if not main_rs.is_file():
        return []
    by_name: dict[str, Command] = {}
    for command in extract_commands(main_rs.read_text()):
        by_name.setdefault(command.name, command)
    return sorted(by_name.values(), key=lambda c: c.name)


def public_commands(commands: list[Command], *, include_internal: bool = False) -> list[Command]:
    """Filter out reflex-desktop's own bridge commands unless explicitly included.

    Args:
        commands: All parsed commands.
        include_internal: Keep ``reflex_desktop_*`` bridge commands when ``True``.

    Returns:
        The commands to expose as typed bindings.
    """
    if include_internal:
        return list(commands)
    return [c for c in commands if not c.name.startswith(_INTERNAL_PREFIX)]


_GENERATED_HEADER = '''"""Typed Tauri command bindings — generated by reflex-desktop.

Do not edit by hand. Regenerate after changing a `#[tauri::command]` with:

    reflex-desktop codegen

Each function returns a Reflex event; pass ``callback`` to route the command's return value
into an event handler. See the README for the `rename_all = "snake_case"` recommendation.
"""
# ruff: noqa: E501

from __future__ import annotations

from typing import Any

import reflex as rx
from reflex.event import EventType

from reflex_desktop import desktop

__all__ = [{all_names}]
'''


def _render_command(command: Command, internal: bool) -> str:
    """Render one typed wrapper function for a command."""
    params = "".join(f"{a.name}: {a.python_type}, " for a in command.arguments)
    signature = f"def {command.name}({params}*, callback: EventType[Any] | None = None)"
    if command.arguments:
        args_obj = ", ".join(f'"{a.name}": {a.name}' for a in command.arguments)
        invoke = f'desktop.invoke("{command.name}", {{{args_obj}}}, callback=callback)'
    else:
        invoke = f'desktop.invoke("{command.name}", callback=callback)'
    note = " (internal reflex-desktop bridge command)" if internal else ""
    doc = (
        f'    """Call the `{command.name}` Tauri command{note}.\n\n'
        f"    Returns (passed to ``callback``): {command.return_type}\n"
        f'    """'
    )
    return f"{signature} -> rx.event.EventSpec:\n{doc}\n    return {invoke}\n"


def render_module(commands: list[Command], *, include_internal: bool = False) -> str:
    """Render a Python module of typed wrappers for the given commands.

    Args:
        commands: Commands to expose.
        include_internal: Include reflex-desktop's own bridge commands (``reflex_desktop_*``).
            Off by default — those have first-class helpers in ``reflex_desktop.desktop``.

    Returns:
        Python source for the generated bindings module.
    """
    public = public_commands(commands, include_internal=include_internal)
    all_names = ", ".join(f'"{c.name}"' for c in public)
    header = _GENERATED_HEADER.format(all_names=all_names)
    bodies = "\n\n".join(_render_command(c, c.name.startswith(_INTERNAL_PREFIX)) for c in public)
    return f"{header}\n\n{bodies}\n" if bodies else f"{header}\n"
