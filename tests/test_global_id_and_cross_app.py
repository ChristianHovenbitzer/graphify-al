"""Tests for bc-code-atlas #26 (global_id resolver) and #27 (cross-app
boundary filter).

#26: `global_id` was already computed on every AL node at extraction time but
unreachable through MCP -- no tool could look a node up *by* it. Covers the
new `_find_node_by_global_id` resolver.

#27: no way to tell whether an edge crosses an app boundary. Covers the new
`al_owning_app` node stamp (via `extract`/`_al_owning_app`) and the
`_crosses_app_boundary`/`_filter_graph_by_context("cross_app")` filtering it
enables.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

pytest.importorskip("tree_sitter_al")

from graphify.build import build_from_json  # noqa: E402
from graphify.extract import extract  # noqa: E402
from graphify.serve import (  # noqa: E402
    _crosses_app_boundary,
    _filter_graph_by_context,
    _find_node_by_global_id,
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _write_app_json(tmp_path: Path, publisher: str, name: str) -> None:
    (tmp_path / "app.json").write_text(
        json.dumps({"publisher": publisher, "name": name}), encoding="utf-8"
    )


def test_al_owning_app_stamped_from_nearest_app_json(tmp_path: Path) -> None:
    _write_app_json(tmp_path, "Contoso", "My App")
    p = _write(tmp_path, "ObjA.al", 'codeunit 50100 "My Codeunit"\n{\n}\n')

    result = extract([p], cache_root=tmp_path / "cache")
    G = build_from_json(result)

    object_nodes = [d for _, d in G.nodes(data=True) if d.get("label", "").startswith("Codeunit")]
    assert object_nodes
    assert object_nodes[0]["al_owning_app"] == "app:Contoso::My App"


def test_al_owning_app_absent_without_app_json(tmp_path: Path) -> None:
    # No app.json anywhere above this file.
    p = _write(tmp_path, "ObjA.al", 'codeunit 50100 "My Codeunit"\n{\n}\n')

    result = extract([p], cache_root=tmp_path / "cache")
    G = build_from_json(result)

    object_nodes = [d for _, d in G.nodes(data=True) if d.get("label", "").startswith("Codeunit")]
    assert object_nodes
    assert "al_owning_app" not in object_nodes[0]


def test_find_node_by_global_id_exact_match(tmp_path: Path) -> None:
    _write_app_json(tmp_path, "Contoso", "My App")
    p = _write(tmp_path, "ObjA.al", 'codeunit 50100 "My Codeunit"\n{\n}\n')

    result = extract([p], cache_root=tmp_path / "cache")
    G = build_from_json(result)

    object_nodes = [
        (nid, d) for nid, d in G.nodes(data=True) if d.get("label", "").startswith("Codeunit")
    ]
    nid, d = object_nodes[0]
    global_id = d["global_id"]

    assert _find_node_by_global_id(G, global_id) == [nid]


def test_find_node_by_global_id_no_match_returns_empty(tmp_path: Path) -> None:
    _write_app_json(tmp_path, "Contoso", "My App")
    p = _write(tmp_path, "ObjA.al", 'codeunit 50100 "My Codeunit"\n{\n}\n')
    result = extract([p], cache_root=tmp_path / "cache")
    G = build_from_json(result)

    assert _find_node_by_global_id(G, "al://nope/codeunit/does not exist") == []


def test_crosses_app_boundary_true_for_different_apps() -> None:
    G = nx.MultiDiGraph()
    G.add_node("a", al_owning_app="app:Vendor1::AppA")
    G.add_node("b", al_owning_app="app:Vendor2::AppB")

    assert _crosses_app_boundary(G, "a", "b") is True


def test_crosses_app_boundary_false_for_same_app() -> None:
    G = nx.MultiDiGraph()
    G.add_node("a", al_owning_app="app:Vendor1::AppA")
    G.add_node("b", al_owning_app="app:Vendor1::AppA")

    assert _crosses_app_boundary(G, "a", "b") is False


def test_crosses_app_boundary_false_when_either_side_unknown() -> None:
    G = nx.MultiDiGraph()
    G.add_node("a", al_owning_app="app:Vendor1::AppA")
    G.add_node("b")  # no al_owning_app -- e.g. a non-AL node

    assert _crosses_app_boundary(G, "a", "b") is False


def test_filter_graph_by_context_cross_app_only_keeps_boundary_edges() -> None:
    G = nx.MultiDiGraph()
    G.add_node("a", al_owning_app="app:Vendor1::AppA")
    G.add_node("b", al_owning_app="app:Vendor2::AppB")
    G.add_node("c", al_owning_app="app:Vendor1::AppA")
    G.add_edge("a", "b", key=0, context="al_calls")  # crosses -- keep
    G.add_edge("a", "c", key=0, context="al_calls")  # same app -- drop

    H = _filter_graph_by_context(G, ["cross_app"])

    edges = list(H.edges())
    assert ("a", "b") in edges
    assert ("a", "c") not in edges


def test_filter_graph_by_context_cross_app_combined_with_relation_kind() -> None:
    G = nx.MultiDiGraph()
    G.add_node("a", al_owning_app="app:Vendor1::AppA")
    G.add_node("b", al_owning_app="app:Vendor2::AppB")
    G.add_edge("a", "b", key=0, context="al_calls")  # cross-app + matching kind -- keep
    G.add_edge("a", "b", key=1, context="al_extends")  # cross-app but wrong kind -- drop

    H = _filter_graph_by_context(G, ["cross_app", "al_calls"])

    edges_with_context = {(u, v, d["context"]) for u, v, d in H.edges(data=True)}
    assert ("a", "b", "al_calls") in edges_with_context
    assert ("a", "b", "al_extends") not in edges_with_context
