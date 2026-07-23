from __future__ import annotations

from pathlib import Path

from graphify.extract import extract_al

# Synthetic AL only (invented 50xxx objects): a table whose fields cover the
# distinct FieldClass values (Normal / FlowField / FlowFilter) and a spread of
# data types (Code, Decimal, Boolean, Integer, Date, Enum), plus a tableextension
# field so the attributes are asserted on both member origins (#38).
_AL_SOURCE = """table 50100 "Widget" {
    fields {
        field(1; "No."; Code[20]) { }
        field(2; Amount; Decimal) { }
        field(3; Active; Boolean) { }
        field(4; "Item Type"; Enum "Widget Type") { }
        field(5; "Line Count"; Integer) { FieldClass = FlowField; CalcFormula = count("Sales Line"); }
        field(6; "Date Filter"; Date) { FieldClass = FlowFilter; }
    }
    keys { key(PK; "No.") { } }
}

enum 50101 "Widget Type" {
    value(0; Standard) { }
    value(1; Premium) { }
}

tableextension 50102 "Widget Ext" extends "Widget" {
    fields {
        field(50100; "Extra Note"; Text[50]) { }
    }
}
"""


def _extract(tmp_path: Path) -> dict:
    p = tmp_path / "Widget.al"
    p.write_text(_AL_SOURCE, encoding="utf-8")
    return extract_al(p)


def _by_label(result: dict) -> dict[str, dict]:
    return {n["label"]: n for n in result["nodes"]}


def test_field_nodes_carry_data_type(tmp_path):
    nodes = _by_label(_extract(tmp_path))

    assert nodes['."No."']["type"] == "Code[20]"
    assert nodes[".Amount"]["type"] == "Decimal"
    assert nodes[".Active"]["type"] == "Boolean"
    assert nodes['."Item Type"']["type"] == 'Enum "Widget Type"'
    assert nodes['."Line Count"']["type"] == "Integer"
    assert nodes['."Date Filter"']["type"] == "Date"


def test_field_class_defaults_to_normal(tmp_path):
    nodes = _by_label(_extract(tmp_path))

    for lbl in ('."No."', ".Amount", ".Active", '."Item Type"'):
        assert nodes[lbl]["field_class"] == "Normal"


def test_flowfield_and_flowfilter_field_class(tmp_path):
    nodes = _by_label(_extract(tmp_path))

    assert nodes['."Line Count"']["field_class"] == "FlowField"
    assert nodes['."Date Filter"']["field_class"] == "FlowFilter"


def test_tableextension_field_carries_attrs(tmp_path):
    nodes = _by_label(_extract(tmp_path))

    ext_field = nodes['."Extra Note"']
    assert ext_field["type"] == "Text[50]"
    assert ext_field["field_class"] == "Normal"


def test_enum_values_have_no_field_attrs(tmp_path):
    # Attributes are field-only; enum values must stay untouched.
    nodes = _by_label(_extract(tmp_path))

    for lbl in (".Standard", ".Premium"):
        assert "type" not in nodes[lbl]
        assert "field_class" not in nodes[lbl]
