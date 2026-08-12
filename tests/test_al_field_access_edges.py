"""RED SPEC: field read/write edges + conditional-call attributes (unimplemented).

Specification (these tests define the contract the extractor must meet):

* `reads_field`: FROM the procedure node that reads a table field TO that
  field's member node. Reading means the field appears as a value inside the
  procedure body (e.g. `if Rec."Some Field" <> '' then`). Inside a table
  object, `Rec.<Field>` resolves to the table's own field node.
* `writes_field`: FROM the procedure node that assigns a field TO the field's
  member node. `Rec.Validate("Other Field", x)` is a write (so is a direct
  `:=` assignment, but Validate is the canonical form).
* Conditional dispatch stays on the EXISTING `calls` edge (no new edge type):
  a call executed only inside an `if <Record>."<Field>" then` block gets two
  extra attributes on its `calls` edge: `conditional` (boolean `True`) and
  `condition_field` (the unquoted field name as a string, e.g.
  `"Feature Enabled"` -> `Feature Enabled`).
* Negative cases: an unconditional call carries NO `conditional` key at all
  (not `False` - the key is simply absent), and a procedure-local variable
  that shadows a field name produces NO `reads_field` edge.

These tests are intentionally red (TDD). They carry the `red_spec` marker and
are deselected by the default `pytest` run (`addopts = -m "not red_spec"` in
pyproject.toml); run them with `pytest -m red_spec`.

Fixtures are fully synthetic: invented 50xxx objects, Business Central
standard patterns only.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract  # noqa: E402

pytestmark = pytest.mark.red_spec


_DOC_TABLE = """table 50100 "My Document"
{
    fields
    {
        field(1; "Some Field"; Code[20]) { }
        field(2; "Other Field"; Code[20]) { }
    }

    procedure CheckSome()
    begin
        if Rec."Some Field" <> '' then
            exit;
    end;

    procedure WriteOther()
    begin
        Rec.Validate("Other Field", 'X');
    end;

    procedure MaybeRun()
    var
        Setup: Record "My Setup";
    begin
        Setup.Get();
        if Setup."Feature Enabled" then
            Target();
    end;

    procedure Always()
    begin
        Target();
    end;

    procedure Target()
    begin
    end;

    procedure LocalShadow()
    var
        "Some Field": Text[20];
    begin
        "Some Field" := 'x';
        if "Some Field" <> '' then
            exit;
    end;
}
"""

_SETUP_TABLE = """table 50101 "My Setup"
{
    fields
    {
        field(1; "Primary Key"; Code[10]) { }
        field(2; "Feature Enabled"; Boolean) { }
    }
}
"""


def _extract(tmp_path: Path) -> dict:
    doc = tmp_path / "MyDocument.Table.al"
    setup = tmp_path / "MySetup.Table.al"
    doc.write_text(_DOC_TABLE, encoding="utf-8")
    setup.write_text(_SETUP_TABLE, encoding="utf-8")
    return extract([doc, setup], cache_root=tmp_path / "cache")


def _by_label(result: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for n in result["nodes"]:
        out.setdefault(n["label"], n)
    return out


def _edges(result: dict, relation: str) -> list[dict]:
    return [e for e in result["edges"] if e.get("relation") == relation]


def _node(result: dict, nid: str) -> dict:
    return next(n for n in result["nodes"] if n["id"] == nid)


def test_field_read_emits_reads_field_edge(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    reads = {(e["source"], e["target"]) for e in _edges(result, "reads_field")}
    assert (nodes[".CheckSome()"]["id"], nodes['."Some Field"']["id"]) in reads


def test_validate_emits_writes_field_edge(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    writes = {(e["source"], e["target"]) for e in _edges(result, "writes_field")}
    assert (nodes[".WriteOther()"]["id"], nodes['."Other Field"']["id"]) in writes


def test_call_inside_field_guard_is_marked_conditional(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    edge = next(
        e for e in _edges(result, "calls")
        if e["source"] == nodes[".MaybeRun()"]["id"]
        and e["target"] == nodes[".Target()"]["id"]
    )
    assert edge.get("conditional") is True
    assert edge.get("condition_field") == "Feature Enabled"


def test_unconditional_call_has_no_conditional_attribute(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    edge = next(
        e for e in _edges(result, "calls")
        if e["source"] == nodes[".Always()"]["id"]
        and e["target"] == nodes[".Target()"]["id"]
    )
    assert "conditional" not in edge
    assert "condition_field" not in edge


def test_local_variable_shadowing_field_name_is_not_a_field_read(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    shadow_id = nodes[".LocalShadow()"]["id"]
    offenders = [e for e in _edges(result, "reads_field") if e["source"] == shadow_id]
    assert offenders == [], (
        "local var shadowing a field name must not read the field: "
        + ", ".join(_node(result, e["target"])["label"] for e in offenders)
    )
