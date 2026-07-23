from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _label(result: dict, nid: str) -> str:
    for n in result["nodes"]:
        if n["id"] == nid:
            return n.get("label", "")
    return f"<{nid}>"


def _calls_edges(result: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for e in result["edges"]:
        if e.get("relation") == "calls":
            out.add((_label(result, e["source"]), _label(result, e["target"])))
    return out


def test_al_var_section_field_call_resolves(tmp_path: Path):
    # AL's dominant "impl codeunit" pattern: a field declared in the object-level
    # var section, called from a procedure. tree-sitter-al nests the declaration
    # one level deeper than a bare parameter (var_section -> var_body ->
    # variable_declaration), which collect_vars previously never descended into,
    # so this call silently produced zero edges.
    impl = _write(tmp_path / "MyImpl.Codeunit.al", (
        'codeunit 50101 "My Impl"\n'
        "{\n"
        "    procedure DoThing()\n"
        "    begin\n"
        "    end;\n"
        "}\n"
    ))
    caller = _write(tmp_path / "Test.Codeunit.al", (
        'codeunit 50100 "Test"\n'
        "{\n"
        "    var\n"
        '        MyImpl: Codeunit "My Impl";\n\n'
        "    procedure Foo()\n"
        "    begin\n"
        "        MyImpl.DoThing();\n"
        "    end;\n"
        "}\n"
    ))
    result = extract([impl, caller], cache_root=tmp_path / "cache")

    assert (".Foo()", ".DoThing()") in _calls_edges(result)


def test_al_var_section_local_call_resolves(tmp_path: Path):
    # Same nesting gap, but for a procedure-local `var` (not an object-level field).
    impl = _write(tmp_path / "MyImpl.Codeunit.al", (
        'codeunit 50101 "My Impl"\n'
        "{\n"
        "    procedure DoThing()\n"
        "    begin\n"
        "    end;\n"
        "}\n"
    ))
    caller = _write(tmp_path / "Test.Codeunit.al", (
        'codeunit 50100 "Test"\n'
        "{\n"
        "    procedure Foo()\n"
        "    var\n"
        '        MyImpl: Codeunit "My Impl";\n'
        "    begin\n"
        "        MyImpl.DoThing();\n"
        "    end;\n"
        "}\n"
    ))
    result = extract([impl, caller], cache_root=tmp_path / "cache")

    assert (".Foo()", ".DoThing()") in _calls_edges(result)


def test_al_parameter_typed_call_still_resolves(tmp_path: Path):
    # Regression guard: parameter-typed calls (parameter_list -> parameter, no
    # extra wrapper node) already worked before this fix and must keep working.
    impl = _write(tmp_path / "MyImpl.Codeunit.al", (
        'codeunit 50101 "My Impl"\n'
        "{\n"
        "    procedure DoThing()\n"
        "    begin\n"
        "    end;\n"
        "}\n"
    ))
    caller = _write(tmp_path / "Test.Codeunit.al", (
        'codeunit 50100 "Test"\n'
        "{\n"
        '    procedure Foo(var MyImpl: Codeunit "My Impl")\n'
        "    begin\n"
        "        MyImpl.DoThing();\n"
        "    end;\n"
        "}\n"
    ))
    result = extract([impl, caller], cache_root=tmp_path / "cache")

    assert (".Foo()", ".DoThing()") in _calls_edges(result)
