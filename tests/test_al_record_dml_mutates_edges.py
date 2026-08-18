"""AL record DML -> `mutates` edges (procedure -> table it writes).

`SalesHeader.Delete(true)` / `SalesLine.DeleteAll()` / `PayLine.Insert(true)` write
rows of the table the record variable is typed as. Field access (`accesses_field`)
and calls (`calls`) never expose that, so "which procedure deletes records of table
X" had no answer in the graph. The record type is resolved via the same var /
parameter / global type table the other AL facts use; an out-of-corpus table falls
back to an `external` stub. Fixtures are fully synthetic (standard BC table names,
invented 50xxx objects).
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract  # noqa: E402


def _mutates(result):
    return [e for e in result["edges"] if e["relation"] == "mutates"]


def _tables(tmp_path):
    hdr = tmp_path / "SalesHeader.Table.al"
    line = tmp_path / "SalesLine.Table.al"
    pay = tmp_path / "PayLine.Table.al"
    hdr.write_text(
        'table 50100 "Sales Header" { fields { field(1; "No."; Code[20]) { } } }\n',
        encoding="utf-8")
    line.write_text(
        'table 50101 "Sales Line" { fields { field(1; "No."; Code[20]) { } } }\n',
        encoding="utf-8")
    pay.write_text(
        'table 50102 "Payment Line" '
        '{ fields { field(1; "No."; Code[20]) { } } }\n',
        encoding="utf-8")
    return hdr, line, pay


def test_mutates_delete_deleteall_insert(tmp_path):
    hdr, line, pay = _tables(tmp_path)
    cu = tmp_path / "PostCu.Codeunit.al"
    cu.write_text(
        'codeunit 50110 "Post Cu"\n'
        '{\n'
        '    var\n'
        '        GlobalPay: Record "Payment Line";\n'
        '\n'
        '    procedure Scrap(var SalesLine: Record "Sales Line")\n'
        '    var\n'
        '        SalesHeader: Record "Sales Header";\n'
        '    begin\n'
        '        SalesHeader.Delete(true);\n'
        '        SalesLine.DeleteAll();\n'
        '        GlobalPay.Insert(false);\n'
        '    end;\n'
        '}\n',
        encoding="utf-8")

    result = extract([hdr, line, pay, cu], cache_root=tmp_path)

    hdr_id = next(n["id"] for n in result["nodes"] if n["label"] == '"Sales Header"')
    line_id = next(n["id"] for n in result["nodes"] if n["label"] == '"Sales Line"')
    pay_id = next(n["id"] for n in result["nodes"]
                  if n["label"] == '"Payment Line"')
    proc_id = next(n["id"] for n in result["nodes"] if n["label"] == ".Scrap()")

    by_target = {e["target"]: e for e in _mutates(result)}
    assert set(by_target) == {hdr_id, line_id, pay_id}

    # Local var, `var` parameter and object-level global must ALL resolve.
    assert by_target[hdr_id]["operation"] == "Delete"
    assert by_target[hdr_id]["run_trigger"] is True
    assert by_target[line_id]["operation"] == "DeleteAll"
    assert "run_trigger" not in by_target[line_id]
    assert by_target[pay_id]["operation"] == "Insert"
    assert by_target[pay_id]["run_trigger"] is False

    for edge in by_target.values():
        assert edge["source"] == proc_id
        assert edge["confidence"] == "EXTRACTED"
        assert edge["source_file"].endswith("PostCu.Codeunit.al")
        assert edge["source_location"].startswith("L")


def test_mutates_unknown_table_becomes_external_stub(tmp_path):
    cu = tmp_path / "PurgeCu.Codeunit.al"
    cu.write_text(
        'codeunit 50111 "Purge Cu"\n'
        '{\n'
        '    procedure Purge()\n'
        '    var\n'
        '        Outside: Record "Item Ledger Entry";\n'
        '    begin\n'
        '        Outside.DeleteAll(true);\n'
        '    end;\n'
        '}\n',
        encoding="utf-8")

    result = extract([cu], cache_root=tmp_path)

    edges = _mutates(result)
    assert len(edges) == 1
    stub = next(n for n in result["nodes"] if n["id"] == edges[0]["target"])
    assert stub["label"] == "Item Ledger Entry"
    assert stub["file_type"] == "external"
    assert stub.get("al_object_type") == "table"
    assert edges[0]["operation"] == "DeleteAll"
    assert edges[0]["run_trigger"] is True


def test_mutates_ignores_non_record_and_unresolvable_receivers(tmp_path):
    cu = tmp_path / "MixCu.Codeunit.al"
    cu.write_text(
        'codeunit 50112 "Mix Cu"\n'
        '{\n'
        '    procedure Go()\n'
        '    var\n'
        '        Helper: Codeunit "Some Helper";\n'
        '    begin\n'
        '        Helper.Insert();\n'
        '        Rec.Delete(true);\n'
        '        Undeclared.DeleteAll();\n'
        '    end;\n'
        '}\n',
        encoding="utf-8")

    result = extract([cu], cache_root=tmp_path)

    # A codeunit-typed receiver stays a `calls` edge; untyped receivers (Rec, an
    # undeclared identifier) yield no edge rather than a guessed one.
    assert _mutates(result) == []


def test_mutates_dedupes_per_operation_not_per_call(tmp_path):
    hdr, line, pay = _tables(tmp_path)
    cu = tmp_path / "TwiceCu.Codeunit.al"
    cu.write_text(
        'codeunit 50113 "Twice Cu"\n'
        '{\n'
        '    procedure Both()\n'
        '    var\n'
        '        SalesLine: Record "Sales Line";\n'
        '    begin\n'
        '        SalesLine.Delete(true);\n'
        '        SalesLine.Delete(true);\n'
        '        SalesLine.Modify(true);\n'
        '    end;\n'
        '}\n',
        encoding="utf-8")

    result = extract([hdr, line, pay, cu], cache_root=tmp_path)

    ops = sorted(e["operation"] for e in _mutates(result))
    assert ops == ["Delete", "Modify"]


def test_mutates_prefers_table_over_same_named_codeunit(tmp_path):
    # AL names are unique per object TYPE, not globally: a codeunit and a table can
    # share a name. `Record "X"` is a table by construction, so the edge must land
    # on the table node even when the codeunit's file is seen first.
    cu_same = tmp_path / "APayLine.Codeunit.al"
    tbl_same = tmp_path / "ZPayLine.Table.al"
    cu = tmp_path / "UseCu.Codeunit.al"
    cu_same.write_text(
        'codeunit 50120 "Payment Line" { procedure Noop() begin end; }\n',
        encoding="utf-8")
    tbl_same.write_text(
        'table 50121 "Payment Line" '
        '{ fields { field(1; "No."; Code[20]) { } } }\n',
        encoding="utf-8")
    cu.write_text(
        'codeunit 50122 "Use Cu"\n'
        '{\n'
        '    procedure Wipe()\n'
        '    var\n'
        '        PayLine: Record "Payment Line";\n'
        '    begin\n'
        '        PayLine.DeleteAll(true);\n'
        '    end;\n'
        '}\n',
        encoding="utf-8")

    result = extract([cu_same, tbl_same, cu], cache_root=tmp_path)

    tbl_id = next(n["id"] for n in result["nodes"]
                  if n.get("al_object_type") == "table"
                  and n["label"] == '"Payment Line"')
    edges = _mutates(result)
    assert len(edges) == 1
    assert edges[0]["target"] == tbl_id
