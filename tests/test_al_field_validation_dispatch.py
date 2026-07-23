"""AL field-validation dispatch on the field node (#26).

Three related behaviours, all resolving to a table field's own graph node:

1. Each field `OnValidate` trigger gets a FIELD-qualified id parented to its
   field node. Previously every field-OnValidate trigger in a table collapsed
   onto one shared id, silently dropping all but one (correctness bug).
2. A `tableextension modify(<Field>) { trigger OnValidate() }` emits a
   `validates` edge from the (field-qualified) modify trigger node to the base
   field's node -- the real node when in-corpus, else an external stub.
3. An `[EventSubscriber(... OnBefore/OnAfterValidateEvent, '<Field>' ...)]`
   resolves its `subscribes` edge to the field node (or stub), not the table.

Fixtures are fully synthetic: standard BC table names and invented 50xxx
objects only.
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract, extract_al  # noqa: E402


# --- Part 1: field-OnValidate id collision -------------------------------------

_TWO_FIELD_TABLE = """table 50100 "Widget" {
    fields {
        field(1; "No."; Code[20]) { trigger OnValidate() begin end; }
        field(2; Name; Text[100]) { trigger OnValidate() begin end; }
    }
}
"""


def test_field_onvalidate_triggers_are_distinct_and_field_parented(tmp_path):
    p = tmp_path / "Widget.al"
    p.write_text(_TWO_FIELD_TABLE, encoding="utf-8")
    result = extract_al(p)

    by_label = {}
    for n in result["nodes"]:
        by_label.setdefault(n["label"], []).append(n)

    no_field = by_label['."No."'][0]
    name_field = by_label[".Name"][0]

    trigger_edges = [e for e in result["edges"] if e["relation"] == "trigger"]
    # One trigger edge per field, each from that field's node.
    by_src = {e["source"]: e["target"] for e in trigger_edges}
    assert no_field["id"] in by_src
    assert name_field["id"] in by_src

    # The two triggers are DISTINCT nodes (collision fixed) ...
    t_no = by_src[no_field["id"]]
    t_name = by_src[name_field["id"]]
    assert t_no != t_name
    trig_nodes = {n["id"]: n for n in result["nodes"]}
    assert trig_nodes[t_no]["label"] == ".OnValidate()"
    assert trig_nodes[t_name]["label"] == ".OnValidate()"
    # ... and field-qualified (parented to the field id, not the object).
    assert t_no.startswith(no_field["id"] + "_")
    assert t_name.startswith(name_field["id"] + "_")

    # No stray object/file-level `OnValidate()` node (the old collapsed form).
    assert "OnValidate()" not in by_label


# --- Part 2: tableextension modify(<Field>) OnValidate -> base field -----------

def test_modify_field_onvalidate_links_to_incorpus_base_field(tmp_path):
    base = tmp_path / "SalesHeader.Table.al"
    ext = tmp_path / "SalesHeaderExt.TableExt.al"
    base.write_text(
        'table 50100 "Sales Header" {\n'
        '    fields { field(1; "No."; Code[20]) { } field(2; Amount; Decimal) { } }\n'
        '}\n',
        encoding="utf-8",
    )
    ext.write_text(
        'tableextension 50110 "Sales Header Ext" extends "Sales Header" {\n'
        '    fields { }\n'
        '    modify(Amount)\n'
        '    {\n'
        '        trigger OnValidate()\n'
        '        begin\n'
        '        end;\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([base, ext], cache_root=tmp_path)

    amount_field = next(
        n for n in result["nodes"]
        if n["label"] == ".Amount" and str(n.get("source_file", "")).endswith("SalesHeader.Table.al")
    )
    validates = [e for e in result["edges"] if e["relation"] == "validates"]
    assert len(validates) == 1
    edge = validates[0]
    assert edge["target"] == amount_field["id"]
    assert edge["context"] == "al_validates_field"
    # The source is the field-qualified modify trigger node in the tableextension.
    src_node = next(n for n in result["nodes"] if n["id"] == edge["source"])
    assert src_node["label"] == ".OnValidate()"


def test_modify_field_onvalidate_external_stub_for_outofcorpus_base(tmp_path):
    # Base table (standard "Customer") is NOT in the corpus -> external field stub.
    ext = tmp_path / "CustomerExt.TableExt.al"
    ext.write_text(
        'tableextension 50110 "Customer Ext" extends Customer {\n'
        '    fields { }\n'
        '    modify(Name)\n'
        '    {\n'
        '        trigger OnValidate()\n'
        '        begin\n'
        '        end;\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([ext], cache_root=tmp_path)

    validates = [e for e in result["edges"] if e["relation"] == "validates"]
    assert len(validates) == 1
    stub = next(n for n in result["nodes"] if n["id"] == validates[0]["target"])
    assert stub["file_type"] == "external"
    assert stub["label"] == "Customer.Name"


# --- Part 3: EventSubscriber to OnAfter/OnBeforeValidateEvent -------------------

def test_subscriber_to_validate_event_resolves_to_incorpus_field(tmp_path):
    tbl = tmp_path / "SalesHeader.Table.al"
    cu = tmp_path / "Sub.Codeunit.al"
    tbl.write_text(
        'table 50100 "Sales Header" {\n'
        '    fields { field(1; "No."; Code[20]) { } field(2; Amount; Decimal) { } }\n'
        '}\n',
        encoding="utf-8",
    )
    cu.write_text(
        'codeunit 50110 "Sub" {\n'
        "    [EventSubscriber(ObjectType::Table, Database::\"Sales Header\", 'OnAfterValidateEvent', 'Amount', false, false)]\n"
        '    local procedure OnValidateAmount(var Rec: Record "Sales Header")\n'
        '    begin\n'
        '    end;\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([tbl, cu], cache_root=tmp_path)

    amount_field = next(
        n for n in result["nodes"]
        if n["label"] == ".Amount" and str(n.get("source_file", "")).endswith("SalesHeader.Table.al")
    )
    subs = [e for e in result["edges"] if e["relation"] == "subscribes"]
    assert len(subs) == 1
    assert subs[0]["target"] == amount_field["id"]


def test_subscriber_to_validate_event_external_field_stub(tmp_path):
    # Subscribes to a validate event on a table outside the corpus.
    cu = tmp_path / "Sub.Codeunit.al"
    cu.write_text(
        'codeunit 50110 "Sub" {\n'
        "    [EventSubscriber(ObjectType::Table, Database::Customer, 'OnBeforeValidateEvent', 'Name', false, false)]\n"
        '    local procedure OnValidateName(var Rec: Record Customer)\n'
        '    begin\n'
        '    end;\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([cu], cache_root=tmp_path)

    subs = [e for e in result["edges"] if e["relation"] == "subscribes"]
    assert len(subs) == 1
    stub = next(n for n in result["nodes"] if n["id"] == subs[0]["target"])
    assert stub["file_type"] == "external"
    assert stub["label"] == "Customer.Name"
