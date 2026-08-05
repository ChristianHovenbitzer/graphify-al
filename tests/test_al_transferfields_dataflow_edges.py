"""AL `TransferFields` -> table-to-table data-flow edge extraction (#4).

`Dest.TransferFields(Source)` copies like-named fields between two records --
an implicit source-table -> dest-table data-flow link (document lineage). Both
the receiver and the first argument are resolved to their record types via the
var/parameter type table (PR #1's `collect_vars`). These tests cover the
var-declared form, the parameter-typed form, and the external-stub fallback for
a source table outside the corpus. Fixtures are fully synthetic (standard BC
table names, invented 50xxx objects).
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract  # noqa: E402


def _xfer_edges(result):
    return [e for e in result["edges"] if e["relation"] == "transfers_to"]


def test_transfers_to_var_declared_source_and_dest(tmp_path):
    src_tbl = tmp_path / "SalesHeader.Table.al"
    dst_tbl = tmp_path / "SalesShptHeader.Table.al"
    cu = tmp_path / "PostCu.Codeunit.al"
    src_tbl.write_text(
        'table 50100 "Sales Header" {\n'
        '    fields { field(1; "No."; Code[20]) { } }\n'
        '}\n',
        encoding="utf-8",
    )
    dst_tbl.write_text(
        'table 50101 "Sales Shipment Header" {\n'
        '    fields { field(1; "No."; Code[20]) { } }\n'
        '}\n',
        encoding="utf-8",
    )
    cu.write_text(
        'codeunit 50110 "Post Cu" {\n'
        '    procedure Ship()\n'
        '    var\n'
        '        SalesHeader: Record "Sales Header";\n'
        '        SalesShptHeader: Record "Sales Shipment Header";\n'
        '    begin\n'
        '        SalesShptHeader.TransferFields(SalesHeader);\n'
        '    end;\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([src_tbl, dst_tbl, cu], cache_root=tmp_path)

    src_id = next(n["id"] for n in result["nodes"] if n["label"] == 'Table 50100 "Sales Header"')
    dst_id = next(n["id"] for n in result["nodes"]
                  if n["label"] == 'Table 50101 "Sales Shipment Header"')

    xfer = _xfer_edges(result)
    assert len(xfer) == 1
    edge = xfer[0]
    # Edge runs SOURCE table -> DESTINATION table (the receiver of TransferFields).
    assert edge["source"] == src_id
    assert edge["target"] == dst_id
    assert edge["confidence"] == "EXTRACTED"
    assert edge["context"] == "al_transfers_to"


def test_transfers_to_parameter_typed_records(tmp_path):
    src_tbl = tmp_path / "SalesHeader.Table.al"
    dst_tbl = tmp_path / "SalesShptHeader.Table.al"
    cu = tmp_path / "PostCu.Codeunit.al"
    src_tbl.write_text(
        'table 50100 "Sales Header" {\n'
        '    fields { field(1; "No."; Code[20]) { } }\n'
        '}\n',
        encoding="utf-8",
    )
    dst_tbl.write_text(
        'table 50101 "Sales Shipment Header" {\n'
        '    fields { field(1; "No."; Code[20]) { } }\n'
        '}\n',
        encoding="utf-8",
    )
    cu.write_text(
        'codeunit 50110 "Post Cu" {\n'
        '    procedure Ship(FromRec: Record "Sales Header"; ToRec: Record "Sales Shipment Header")\n'
        '    begin\n'
        '        ToRec.TransferFields(FromRec);\n'
        '    end;\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([src_tbl, dst_tbl, cu], cache_root=tmp_path)

    src_id = next(n["id"] for n in result["nodes"] if n["label"] == 'Table 50100 "Sales Header"')
    dst_id = next(n["id"] for n in result["nodes"]
                  if n["label"] == 'Table 50101 "Sales Shipment Header"')

    xfer = _xfer_edges(result)
    assert len(xfer) == 1
    assert xfer[0]["source"] == src_id
    assert xfer[0]["target"] == dst_id


def test_transfers_to_unresolved_source_becomes_external_stub(tmp_path):
    dst_tbl = tmp_path / "MyDoc.Table.al"
    cu = tmp_path / "CopyCu.Codeunit.al"
    dst_tbl.write_text(
        'table 50101 "My Doc" {\n'
        '    fields { field(1; "No."; Code[20]) { } }\n'
        '}\n',
        encoding="utf-8",
    )
    cu.write_text(
        'codeunit 50110 "Copy Cu" {\n'
        '    procedure Copy()\n'
        '    var\n'
        '        MyDoc: Record "My Doc";\n'
        '        Cust: Record Customer;\n'
        '    begin\n'
        '        MyDoc.TransferFields(Cust);\n'
        '    end;\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([dst_tbl, cu], cache_root=tmp_path)

    xfer = _xfer_edges(result)
    assert len(xfer) == 1
    stub = next(n for n in result["nodes"] if n["id"] == xfer[0]["source"])
    assert stub["file_type"] == "external"
    assert stub["label"] == "Customer"


def test_transfers_to_skips_rec_and_nonrecord_args(tmp_path):
    dst_tbl = tmp_path / "MyDoc.Table.al"
    cu = tmp_path / "SkipCu.Codeunit.al"
    dst_tbl.write_text(
        'table 50101 "My Doc" {\n'
        '    fields { field(1; "No."; Code[20]) { } }\n'
        '}\n',
        encoding="utf-8",
    )
    cu.write_text(
        'codeunit 50110 "Skip Cu" {\n'
        '    procedure Copy()\n'
        '    var\n'
        '        MyDoc: Record "My Doc";\n'
        '        SomeText: Text;\n'
        '    begin\n'
        '        MyDoc.TransferFields(Rec);\n'
        '        MyDoc.TransferFields(SomeText);\n'
        '    end;\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([dst_tbl, cu], cache_root=tmp_path)

    assert _xfer_edges(result) == []
