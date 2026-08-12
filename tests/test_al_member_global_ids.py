"""RED SPEC: `global_id` on AL member nodes (fields, procedures, triggers).

Object nodes already carry the deterministic cross-graph join key
`al://<qualifier>/<type>/<name>` (see `_al_make_global_id`, #27), but member
nodes are explicitly skipped by the stamping guard in `extract_al` (labels
starting with `.`). No `#member` convention exists in the code yet, so this
file specifies one:

* Member global_id = `<object global_id>#<member-path>`.
* The member path is the member's name, lowercased and unquoted, exactly like
  the object-name normalization in `_al_make_global_id`; procedure names drop
  the trailing `()` signature parens of the display label.
* Nested members chain with `/`: a field trigger is
  `<object global_id>#<field>/<trigger>` (trigger name also without parens).

Example for `namespace Demo.Sales; table 50100 Widget`:

* field `"No."`               -> `al://demo.sales/table/widget#no.`
* procedure `Rename()`        -> `al://demo.sales/table/widget#rename`
* OnValidate of field `"No."` -> `al://demo.sales/table/widget#no./onvalidate`

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

from graphify.extract import extract_al  # noqa: E402

pytestmark = pytest.mark.red_spec


_AL_SOURCE = """namespace Demo.Sales;

table 50100 Widget
{
    fields
    {
        field(1; "No."; Code[20])
        {
            trigger OnValidate()
            begin
            end;
        }
    }

    procedure Rename()
    begin
    end;
}
"""


def _extract(tmp_path: Path) -> dict:
    p = tmp_path / "Widget.Table.al"
    p.write_text(_AL_SOURCE, encoding="utf-8")
    return extract_al(p)


def _by_label(result: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for n in result["nodes"]:
        out.setdefault(n["label"], []).append(n)
    return out


def test_object_node_global_id_unchanged(tmp_path):
    result = _extract(tmp_path)
    obj = _by_label(result)["Widget"][0]
    assert obj.get("global_id") == "al://demo.sales/table/widget"


def test_field_node_has_member_global_id(tmp_path):
    result = _extract(tmp_path)
    field = _by_label(result)['."No."'][0]
    assert field.get("global_id") == "al://demo.sales/table/widget#no."


def test_procedure_node_has_member_global_id(tmp_path):
    result = _extract(tmp_path)
    proc = _by_label(result)[".Rename()"][0]
    assert proc.get("global_id") == "al://demo.sales/table/widget#rename"


def test_field_trigger_node_has_nested_member_global_id(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)
    field = nodes['."No."'][0]
    triggers = [
        n for n in nodes.get(".OnValidate()", [])
        if n["id"].startswith(field["id"] + "_")
    ]
    assert len(triggers) == 1
    assert triggers[0].get("global_id") == "al://demo.sales/table/widget#no./onvalidate"


def test_every_member_node_has_a_global_id(tmp_path):
    result = _extract(tmp_path)
    members = [
        n for n in result["nodes"]
        if str(n.get("label", "")).startswith(".")
    ]
    assert members, "fixture should produce member nodes"
    missing = [n["label"] for n in members if not n.get("global_id")]
    assert missing == [], f"member nodes without global_id: {missing}"
