"""Field access inside a table resolves for all four spellings of Rec.

Table code names its own fields four ways: bare (`Quantity := ...`, implicit
Rec - by far the most common), `Rec.Field`, `xRec.Field` and `this.Field` (on a
table `this` IS Rec). Only the qualified forms were edges, so the dominant
spelling produced nothing and "what does validating this field change" had no
answer. A bare name becomes an edge only when it matches a field the object
declares, so a call target, an enum value or a label cannot turn into one.
Fixtures are fully synthetic (invented 50xxx objects).
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
            begin
                Amount := Round("Quantity (Base)" / Factor, 0.01);
                Validate("No.");
                Helper();
                this.Currency := 'EUR';
                xRec.Kind := 1;
                Rec.Note := 'n';
            end;
        }
        field(3; "Quantity (Base)"; Decimal) { }
        field(4; Factor; Decimal) { }
        field(5; Currency; Code[10]) { }
        field(6; Kind; Integer) { }
        field(7; Note; Text[50]) { }
    }

    procedure Helper() begin end;
}
'''


def _access(tmp_path):
    tbl = tmp_path / "PayLine.Table.al"
    tbl.write_text(_TABLE, encoding="utf-8")
    result = extract([tbl], cache_root=tmp_path)
    lbl = {n["id"]: n.get("label") for n in result["nodes"]}
    modes = {}
    for e in result["edges"]:
        if e.get("relation") == "accesses_field":
            modes[lbl.get(e["target"])] = e.get("mode")
    return result, modes


def test_bare_field_names_resolve_with_read_write_mode(tmp_path):
    _, modes = _access(tmp_path)

    assert modes[".Amount"] == "write"            # bare assignment target
    assert modes['."Quantity (Base)"'] == "read"  # bare read, quoted
    assert modes[".Factor"] == "read"             # bare read, unquoted


def test_bare_validate_is_a_write(tmp_path):
    # `Validate(Field)` without a Rec. prefix must not be recorded as a read -
    # that would invert the direction of the edge.
    _, modes = _access(tmp_path)

    assert modes['."No."'] == "write"


def test_this_xrec_and_rec_all_resolve(tmp_path):
    _, modes = _access(tmp_path)

    assert modes[".Currency"] == "write"  # this.Field
    assert modes[".Kind"] == "write"      # xRec.Field
    assert modes[".Note"] == "write"      # Rec.Field


def test_a_procedure_call_is_not_a_field(tmp_path):
    result, modes = _access(tmp_path)

    assert ".Helper()" not in modes
    calls = [e for e in result["edges"] if e.get("relation") == "calls"]
    assert calls, "the Helper() call must still be a calls edge"


def test_no_duplicate_edges(tmp_path):
    result, _ = _access(tmp_path)

    counted = collections.Counter(
        (e["source"], e["target"], e.get("relation"), e.get("mode"))
        for e in result["edges"]
    )
    assert [k for k, v in counted.items() if v > 1] == []
