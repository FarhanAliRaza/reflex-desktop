"""Unit tests for the Rust -> Python command codegen (no Rust/Tauri toolchain required)."""

from __future__ import annotations

from reflex_desktop import codegen
from reflex_desktop.codegen import Argument, extract_commands, rust_type_to_python


def test_rust_type_mapping_scalars():
    assert rust_type_to_python("String") == "str"
    assert rust_type_to_python("&str") == "str"
    assert rust_type_to_python("&'a str") == "str"
    assert rust_type_to_python("usize") == "int"
    assert rust_type_to_python("f64") == "float"
    assert rust_type_to_python("bool") == "bool"
    assert rust_type_to_python("std::path::PathBuf") == "str"
    assert rust_type_to_python("()") == "None"


def test_rust_type_mapping_containers():
    assert rust_type_to_python("Option<String>") == "str | None"
    assert rust_type_to_python("Vec<i32>") == "list[int]"
    assert rust_type_to_python("Result<String, String>") == "str"
    assert rust_type_to_python("Result<Vec<u8>, MyError>") == "list[int]"
    assert rust_type_to_python("HashMap<String, i64>") == "dict[str, int]"
    assert rust_type_to_python("Option<Vec<String>>") == "list[str] | None"


def test_rust_type_mapping_unknown_falls_back_to_any():
    assert rust_type_to_python("MyCustomStruct") == "Any"
    assert rust_type_to_python("serde_json::Value") == "Any"


def test_extract_simple_command():
    src = """
#[tauri::command]
fn greet(name: String) -> String {
    format!("Hello, {}!", name)
}
"""
    (cmd,) = extract_commands(src)
    assert cmd.name == "greet"
    assert cmd.arguments == (Argument("name", "str"),)
    assert cmd.return_type == "str"


def test_extract_drops_injected_arguments():
    """AppHandle / Window / State are injected by Tauri, not passed from JS."""
    src = """
#[tauri::command]
async fn save(
    app: tauri::AppHandle,
    window: Window,
    state: State<'_, Db>,
    path: String,
    contents: Option<String>,
) -> Result<(), String> {
    Ok(())
}
"""
    (cmd,) = extract_commands(src)
    assert cmd.name == "save"
    assert cmd.arguments == (Argument("path", "str"), Argument("contents", "str | None"))
    assert cmd.return_type == "None"


def test_extract_no_args_and_no_return():
    src = """
#[tauri::command]
pub fn ping() {}
"""
    (cmd,) = extract_commands(src)
    assert cmd.name == "ping"
    assert cmd.arguments == ()
    assert cmd.return_type == "None"


def test_extract_handles_rename_all_attribute_and_multiple():
    src = """
#[tauri::command(rename_all = "snake_case")]
fn read_file(file_path: String) -> Result<String, String> { todo!() }

// not a command
fn helper() {}

#[tauri::command]
fn add(a: i32, b: i32) -> i32 { a + b }
"""
    cmds = {c.name: c for c in extract_commands(src)}
    assert set(cmds) == {"read_file", "add"}
    assert cmds["read_file"].arguments == (Argument("file_path", "str"),)
    assert cmds["add"].return_type == "int"


def test_render_module_is_valid_python_and_exposes_public_commands():
    from reflex_desktop.codegen import Command

    commands = [
        Command("greet", (Argument("name", "str"),), "str"),
        Command("reflex_desktop_notify", (Argument("title", "str"),), "None"),
    ]
    module = codegen.render_module(commands)
    # Internal bridge commands are excluded by default.
    assert "def greet(" in module
    assert "reflex_desktop_notify" not in module
    assert 'desktop.invoke("greet", {"name": name}, callback=callback)' in module
    # The generated source must at least parse.
    compile(module, "<generated>", "exec")


def test_extract_handles_closing_bracket_inside_attribute_string():
    """A `]` inside the command attribute must not end attribute parsing early."""
    src = """
#[tauri::command(rename_all = "snake_case")]
fn ok(value: String) -> String { value }

#[tauri::command(some_attr = "has ] bracket")]
fn weird(x: i32) -> i32 { x }
"""
    cmds = {c.name: c for c in extract_commands(src)}
    assert set(cmds) == {"ok", "weird"}
    assert cmds["weird"].arguments == (Argument("x", "int"),)


def test_extract_strips_where_clause_from_return_type():
    """A `where` clause must not leak into the parsed return type."""
    src = """
#[tauri::command]
fn fetch(url: String) -> String
where
    Self: Sized,
{
    url
}
"""
    (cmd,) = extract_commands(src)
    assert cmd.return_type == "str"


def test_discover_commands_reads_only_main_rs(tmp_path):
    """Discovery is scoped to main.rs — commands in sibling modules are ignored."""
    src = tmp_path / "src-tauri" / "src"
    src.mkdir(parents=True)
    (src / "main.rs").write_text("#[tauri::command]\nfn in_main() {}\n")
    (src / "other.rs").write_text("#[tauri::command]\nfn in_other() {}\n")

    names = {c.name for c in codegen.discover_commands(tmp_path / "src-tauri")}
    assert names == {"in_main"}


def test_render_module_can_include_internal():
    from reflex_desktop.codegen import Command

    module = codegen.render_module(
        [Command("reflex_desktop_notify", (Argument("title", "str"),), "None")],
        include_internal=True,
    )
    assert "def reflex_desktop_notify(" in module
    compile(module, "<generated>", "exec")
