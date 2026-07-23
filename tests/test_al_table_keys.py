from __future__ import annotations

from pathlib import Path

from graphify.extract import extract_al

# Synthetic AL only (invented 50xxx objects): a table whose keys section carries
# a clustered primary key and a secondary key with SumIndexFields, plus a
# tableextension adding a key. The `Name` key deliberately shares its name with a
# field to prove keys and fields never collide on the same table.
_AL_SOURCE = """table 50100 "Widget" {
    fields {
        field(1; "No."; Code[20]) { }
        field(2; Name; Text[100]) { }
        field(3; Balance; Decimal) { }
    }
    keys {
        key(PK; "No.") { Clustered = true; }
        key(Name; Name, Balance) { SumIndexFields = Balance; }
    }
}

tableextension 50101 "Widget Ext" extends "Widget" {
    fields {
        field(50100; "Region Code"; Code[10]) { }
    }
    keys {
        key(RegionKey; "Region Code") { }
    }
}
"""


def _extract(tmp_path: Path) -> dict:
    p = tmp_path / "WidgetKeys.al"
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


def test_keys_are_nodes_parented_to_table(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    table = nodes['"Widget"']
    pk = nodes[".key(PK)"]
    sec = nodes[".key(Name)"]

    # Keys are AL member nodes, sourced to the same file as their table.
    assert pk["al_member_kind"] == "key"
    assert sec["al_member_kind"] == "key"
    assert pk["source_file"] == table["source_file"]

    # Parent-qualified id and a `contains` edge from the table, exactly like
    # field/enum member nodes.
    contains = _contains_edges(result)
    assert (table["id"], pk["id"]) in contains
    assert (table["id"], sec["id"]) in contains
    assert pk["id"].startswith(table["id"] + "_")
    assert sec["id"].startswith(table["id"] + "_")


def test_key_fields_and_sumindexfields_captured(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    pk = nodes[".key(PK)"]
    assert pk["key_fields"] == ['"No."']
    assert pk["clustered"] is True
    assert "sumindexfields" not in pk

    sec = nodes[".key(Name)"]
    assert sec["key_fields"] == ["Name", "Balance"]
    assert sec["sumindexfields"] == ["Balance"]
    assert "clustered" not in sec


def test_key_does_not_collide_with_same_named_field(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    # The `Name` field member and the `Name` key are distinct nodes with
    # distinct ids under the same table.
    field_name = nodes[".Name"]
    key_name = nodes[".key(Name)"]
    assert field_name["id"] != key_name["id"]
    assert field_name.get("al_member_kind") != "key"


def test_tableextension_keys_are_extracted(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    ext = nodes['"Widget Ext"']
    rk = nodes[".key(RegionKey)"]
    assert rk["al_member_kind"] == "key"
    assert rk["key_fields"] == ['"Region Code"']

    contains = _contains_edges(result)
    assert (ext["id"], rk["id"]) in contains
