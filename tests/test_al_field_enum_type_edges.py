"""AL Enum-typed field -> enum object edge extraction (#29).

An `Enum "<Name>"`-typed field references an enum object, but nothing linked the
field to it. These tests cover the `typed_as` edge: it originates from the FIELD
member node (not the table) and points at the enum object node, with an external
stub fallback when the enum lives outside the corpus. Plain Option-typed fields
name no enum object and emit no edge. Fixtures are fully synthetic (invented
50xxx objects).
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract, extract_al  # noqa: E402


def _typed_edges(result):
    return [e for e in result["edges"] if e["relation"] == "typed_as"]


def test_collect_facts_enum_field_only():
    src = b'''table 50100 "My Order" {
    fields {
        field(1; "No."; Code[20]) { }
        field(2; "Contact Type"; Enum "Contact Type") { }
        field(3; State; Enum MyState) { }
        field(4; Status; Option) { OptionMembers = A,B,C; }
    }
}
'''
    import tree_sitter_al as tsal
    from tree_sitter import Language, Parser
    from graphify.extract import _al_collect_facts

    tree = Parser(Language(tsal.language())).parse(src)
    facts = _al_collect_facts(tree, src)
    typed = sorted(f["target"] for f in facts if f["kind"] == "typed_as")
    # Both Enum-typed fields (quoted + bare); the Option field names no enum.
    assert typed == ["Contact Type", "MyState"]


def test_typed_as_edge_resolves_from_field_to_enum_node(tmp_path):
    order = tmp_path / "MyOrder.Table.al"
    ctype = tmp_path / "ContactType.Enum.al"
    order.write_text(
        'table 50100 "My Order" {\n'
        '    fields {\n'
        '        field(1; "No."; Code[20]) { }\n'
        '        field(2; "Contact Type"; Enum "Contact Type") { }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )
    ctype.write_text(
        'enum 50101 "Contact Type" {\n'
        '    value(0; Person) { }\n'
        '    value(1; Company) { }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([order, ctype], cache_root=tmp_path)

    enum_obj = next(n["id"] for n in result["nodes"] if n["label"] == 'Enum 50101 "Contact Type"')
    field_node = next(n["id"] for n in result["nodes"] if n["label"] == '."Contact Type"')

    typed = _typed_edges(result)
    assert len(typed) == 1
    edge = typed[0]
    # Edge originates from the FIELD member node (mirror of relates_to, but the
    # field is the source, not the table) and lands on the enum object node.
    assert edge["source"] == field_node
    assert edge["target"] == enum_obj
    assert edge["confidence"] == "EXTRACTED"
    assert edge["context"] == "al_typed_as"


def test_typed_as_unresolved_enum_becomes_external_stub(tmp_path):
    order = tmp_path / "MyOrder.Table.al"
    order.write_text(
        'table 50100 "My Order" {\n'
        '    fields {\n'
        '        field(2; "Contact Type"; Enum "Contact Type") { }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([order], cache_root=tmp_path)

    typed = _typed_edges(result)
    assert len(typed) == 1
    stub = next(n for n in result["nodes"] if n["id"] == typed[0]["target"])
    assert stub["file_type"] == "external"
    assert stub["label"] == "Contact Type"


def test_option_typed_field_emits_no_edge(tmp_path):
    order = tmp_path / "MyOrder.Table.al"
    order.write_text(
        'table 50100 "My Order" {\n'
        '    fields {\n'
        '        field(1; Status; Option) { OptionMembers = A,B,C; }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([order], cache_root=tmp_path)

    assert _typed_edges(result) == []
