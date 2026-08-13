"""AL field `TableRelation` -> target-table edge extraction (#12).

A field's `TableRelation` property names the foreign-key target table. These
tests cover the simple bare/quoted forms, the field-qualified form, the
conditional `if ... else` form, and the external-stub fallback for a target
outside the corpus. Fixtures are fully synthetic (invented 50xxx objects).
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract, extract_al  # noqa: E402


def _rel_edges(result):
    return [e for e in result["edges"] if e["relation"] == "relates_to"]


def test_collect_facts_simple_and_conditional():
    src = b'''table 50100 "My Order" {
    fields {
        field(1; "No."; Code[20]) { }
        field(3; "Terms Code"; Code[10]) { TableRelation = "My Terms"; }
        field(4; "Cust No"; Code[20]) { TableRelation = MyCustomer; }
        field(5; "Item No"; Code[20]) { TableRelation = MyItem."No."; }
        field(6; "Src No"; Code[20]) { TableRelation = if ("Src Type" = const(0)) MyItem else MyCustomer; }
    }
}
'''
    import tree_sitter_al as tsal
    from tree_sitter import Language, Parser
    from graphify.extract import _al_collect_facts

    tree = Parser(Language(tsal.language())).parse(src)
    facts = _al_collect_facts(tree, src)
    rel = sorted(f["target"] for f in facts if f["kind"] == "relates_to")
    # quoted, bare, field-qualified, and both conditional branches (space sorts first)
    assert rel == ["My Terms", "MyCustomer", "MyCustomer", "MyItem", "MyItem"]


def test_relates_to_edges_resolve_to_target_table_node(tmp_path):
    order = tmp_path / "MyOrder.Table.al"
    terms = tmp_path / "MyTerms.Table.al"
    order.write_text(
        'table 50100 "My Order" {\n'
        '    fields {\n'
        '        field(1; "No."; Code[20]) { }\n'
        '        field(3; "Terms Code"; Code[10]) { TableRelation = "My Terms"; }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )
    terms.write_text(
        'table 50101 "My Terms" {\n'
        '    fields {\n'
        '        field(1; "Code"; Code[10]) { }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([order, terms], cache_root=tmp_path)

    terms_obj = next(n["id"] for n in result["nodes"] if n["label"] == 'Table 50101 "My Terms"')

    rel = _rel_edges(result)
    assert len(rel) == 1
    edge = rel[0]
    # Source resolves within the referencing (order) file; target is the FK table.
    # (Object declared on line 1 resolves to the file node, matching `extends`.)
    assert edge["source_file"].endswith("MyOrder.Table.al")
    assert edge["target"] == terms_obj
    assert edge["confidence"] == "EXTRACTED"
    assert edge["context"] == "al_relates_to"


def test_relates_to_unresolved_target_becomes_external_stub(tmp_path):
    order = tmp_path / "MyOrder.Table.al"
    order.write_text(
        'table 50100 "My Order" {\n'
        '    fields {\n'
        '        field(3; "Terms Code"; Code[10]) { TableRelation = "Payment Terms"; }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([order], cache_root=tmp_path)

    rel = _rel_edges(result)
    assert len(rel) == 1
    stub = next(n for n in result["nodes"] if n["id"] == rel[0]["target"])
    assert stub["file_type"] == "external"
    assert stub["label"] == "Payment Terms"
