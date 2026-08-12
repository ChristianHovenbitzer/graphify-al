"""RED SPEC: AL Label declarations -> `raises` edges (not yet implemented).

Specification (these tests define the contract the extractor must meet):

* Every `Label` variable declared in an object-level `var` section becomes a
  first-class member node, exactly like fields and procedures: label text is
  the var name prefixed with a dot (`.MyCheckErr`), the node id is parented
  under the object id, and the object links to it with a `contains` edge.
* The label node carries the original label string under the existing
  `al_label` attribute key (the same key `_al_collect_node_text` already uses
  for label text, but on the label's OWN node instead of the object node).
* Direction/name of the new edge: `raises`, FROM the procedure node that
  executes `Error(<LabelName>)` TO the label node. One edge per throwing
  procedure, so a label thrown from two procedures has two incoming `raises`
  edges.
* A declared label that is never passed to `Error(...)` still gets its node
  (and `contains` edge) but NO incoming `raises` edge.
* `Error('inline literal')` produces NO node and NO edge: only declared
  Labels are graph-worthy; inline literals stay invisible (no synthetic
  nodes).

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


_AL_SOURCE = """table 50100 "My Gadget"
{
    fields
    {
        field(1; "No."; Code[20]) { }
    }

    var
        MyCheckErr: Label 'Something is not allowed.';
        SharedErr: Label 'Shared failure.';
        UnusedErr: Label 'Never thrown.';

    procedure FailIfSomething()
    begin
        Error(MyCheckErr);
    end;

    procedure FailA()
    begin
        Error(SharedErr);
    end;

    procedure FailB()
    begin
        Error(SharedErr);
    end;

    procedure FailInline()
    begin
        Error('Inline failure text.');
    end;
}
"""


def _extract(tmp_path: Path) -> dict:
    p = tmp_path / "MyGadget.Table.al"
    p.write_text(_AL_SOURCE, encoding="utf-8")
    return extract_al(p)


def _by_label(result: dict) -> dict[str, dict]:
    return {n["label"]: n for n in result["nodes"]}


def _edges(result: dict, relation: str) -> list[dict]:
    return [e for e in result["edges"] if e.get("relation") == relation]


def test_label_vars_are_member_nodes_with_text(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    obj = nodes['"My Gadget"']
    for name, text in (
        (".MyCheckErr", "Something is not allowed."),
        (".SharedErr", "Shared failure."),
        (".UnusedErr", "Never thrown."),
    ):
        assert name in nodes, f"label member node {name} missing"
        node = nodes[name]
        assert node["id"].startswith(obj["id"] + "_"), "label node not object-parented"
        assert node.get("al_label") == text, f"{name}: original label text missing"

    contains = {(e["source"], e["target"]) for e in _edges(result, "contains")}
    for name in (".MyCheckErr", ".SharedErr", ".UnusedErr"):
        assert (obj["id"], nodes[name]["id"]) in contains


def test_error_call_emits_raises_edge_procedure_to_label(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    raises = {(e["source"], e["target"]) for e in _edges(result, "raises")}
    assert (nodes[".FailIfSomething()"]["id"], nodes[".MyCheckErr"]["id"]) in raises


def test_label_thrown_from_two_procedures_has_two_raises_edges(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    shared_id = nodes[".SharedErr"]["id"]
    sources = {e["source"] for e in _edges(result, "raises") if e["target"] == shared_id}
    assert sources == {nodes[".FailA()"]["id"], nodes[".FailB()"]["id"]}


def test_unused_label_gets_node_but_no_raises_edge(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    assert ".UnusedErr" in nodes
    unused_id = nodes[".UnusedErr"]["id"]
    assert not any(e["target"] == unused_id for e in _edges(result, "raises"))


def test_inline_error_literal_emits_no_raises_edge(tmp_path):
    result = _extract(tmp_path)
    nodes = _by_label(result)

    inline_id = nodes[".FailInline()"]["id"]
    assert not any(e["source"] == inline_id for e in _edges(result, "raises"))
    # And no synthetic node for the literal text either.
    assert not any(
        "Inline failure text" in str(n.get("label", "")) for n in result["nodes"]
    )
