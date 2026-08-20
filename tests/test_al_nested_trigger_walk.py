"""Nested triggers (`OnValidate` in a field, `OnAction` in an action) are walked.

Only direct children of an object body are object-wide triggers (`OnRun`,
`OnInsert`, `OnOpenPage`). `OnValidate` sits in `fields{field{trigger}}` and
`OnAction` in `actions{area{action{trigger}}}`, so neither was reached and the
business logic in them produced no `calls` and no `accesses_field` edges. The
object-level bodies must NOT be walked twice by the nested pass, or every edge
out of them doubles. Fixtures are fully synthetic (invented 50xxx objects).
"""
from __future__ import annotations

import collections

import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract  # noqa: E402

_TABLE = '''table 50100 "Payment Line"
{
    fields
    {
        field(1; "No."; Code[20]) { }
        field(2; Amount; Decimal)
        {
            trigger OnValidate()
            var
                PayLine2: Record "Payment Line";
            begin
                PayLine2."No." := 'X';
                Helper();
            end;
        }
    }
    trigger OnInsert()
    var
        PayLine3: Record "Payment Line";
    begin
        PayLine3.Amount := 1;
    end;

    procedure Helper() begin end;
}
'''

_PAGE = '''page 50101 "Pay Card"
{
    SourceTable = "Payment Line";
    layout { area(content) { field(Amt; Rec.Amount) { } } }
    actions { area(processing) { action(Go)
    {
        trigger OnAction()
        var
            PayLine5: Record "Payment Line";
        begin
            PayLine5."No." := 'A';
        end;
    } } }

    trigger OnOpenPage()
    var
        PayLine4: Record "Payment Line";
    begin
        PayLine4.Amount := 2;
    end;
}
'''


def _extract(tmp_path):
    tbl = tmp_path / "PayLine.Table.al"
    pag = tmp_path / "PayCard.Page.al"
    tbl.write_text(_TABLE, encoding="utf-8")
    pag.write_text(_PAGE, encoding="utf-8")
    return extract([tbl, pag], cache_root=tmp_path)


def _by_source(result, relation):
    lbl = {n["id"]: n.get("label") for n in result["nodes"]}
    out = collections.defaultdict(list)
    for e in result["edges"]:
        if e.get("relation") == relation:
            out[lbl.get(e["source"])].append((lbl.get(e["target"]), e.get("mode")))
    return out


def test_nested_and_object_level_triggers_both_emit_field_access(tmp_path):
    acc = _by_source(_extract(tmp_path), "accesses_field")

    # object-level triggers (were already covered)
    assert (".Amount", "write") in acc[".OnInsert()"]
    assert (".Amount", "write") in acc[".OnOpenPage()"]
    # nested triggers (the regression this guards)
    assert ('."No."', "write") in acc[".OnValidate()"]
    assert ('."No."', "write") in acc[".OnAction()"]


def test_nested_trigger_emits_calls(tmp_path):
    calls = _by_source(_extract(tmp_path), "calls")

    assert any(t == ".Helper()" for t, _ in calls[".OnValidate()"])


def test_object_level_trigger_is_not_walked_twice(tmp_path):
    result = _extract(tmp_path)

    counted = collections.Counter(
        (e["source"], e["target"], e.get("relation"), e.get("mode"))
        for e in result["edges"]
    )
    assert [k for k, v in counted.items() if v > 1] == []
