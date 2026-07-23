from __future__ import annotations

from pathlib import Path

from graphify.extract import extract_al

# Synthetic AL only (invented 50xxx objects): a table with two fields, an enum
# with two values, and a codeunit with one procedure so we can assert the
# pre-existing object/procedure modelling is untouched by the new member nodes.
_AL_SOURCE = """table 50100 "Widget" {
    fields {
        field(1; "No."; Code[20]) { }
        field(2; Name; Text[100]) { }
    }
    keys { key(PK; "No.") { } }
}

enum 50101 "Widget Type" {
    value(0; Standard) { }
    value(1; Premium) { }
}

codeunit 50102 "Widget Mgt" {
    procedure DoIt()
    begin
    end;
}
"""


def _extract(tmp_path: Path) -> dict:
    p = tmp_path / "Widget.al"
    p.write_text(_AL_SOURCE, encoding="utf-8")
    return extract_al(p)


def _by_label(result: dict) -> dict[str, dict]:
    return {n["label"]: n for n in result["nodes"]}


def _contains_edges(result: dict) -> set[tuple[str, str]]:
    return {
        (e["source"], e["target"])
        for e in result["edges"]
        if e.get("relation") == "contains"
    }


def test_table_fields_are_first_class_nodes(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    table = nodes['"Widget"']
    assert nodes['."No."']["source_file"] == table["source_file"]
    assert nodes[".Name"]["source_file"] == table["source_file"]

    contains = _contains_edges(result)
    assert (table["id"], nodes['."No."']["id"]) in contains
    assert (table["id"], nodes[".Name"]["id"]) in contains

    # Parent-qualified, exactly like procedure node ids.
    assert nodes['."No."']["id"].startswith(table["id"] + "_")
    assert nodes[".Name"]["id"].startswith(table["id"] + "_")


def test_enum_values_are_first_class_nodes(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    enum = nodes['"Widget Type"']
    contains = _contains_edges(result)
    assert (enum["id"], nodes[".Standard"]["id"]) in contains
    assert (enum["id"], nodes[".Premium"]["id"]) in contains

    assert nodes[".Standard"]["id"].startswith(enum["id"] + "_")
    assert nodes[".Premium"]["id"].startswith(enum["id"] + "_")


def test_existing_object_and_procedure_nodes_unaffected(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    # Object nodes still present.
    for obj in ('"Widget"', '"Widget Type"', '"Widget Mgt"'):
        assert obj in nodes

    # File still `contains` each object (object containment edges intact).
    file_node = next(n for n in result["nodes"] if n["label"].endswith(".al"))
    contains = _contains_edges(result)
    for obj in ('"Widget"', '"Widget Type"', '"Widget Mgt"'):
        assert (file_node["id"], nodes[obj]["id"]) in contains

    # Procedure node still modelled via the `method` edge, not `contains`.
    proc = nodes[".DoIt()"]
    method_edges = {
        (e["source"], e["target"])
        for e in result["edges"]
        if e.get("relation") == "method"
    }
    assert (nodes['"Widget Mgt"']["id"], proc["id"]) in method_edges
    # The procedure is not turned into a `contains` member.
    assert (nodes['"Widget Mgt"']["id"], proc["id"]) not in contains
