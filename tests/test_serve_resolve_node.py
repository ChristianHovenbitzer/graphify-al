"""Tests for deterministic symbol resolution and traversal ergonomics (serve.py).

Ported from upstream (ChristianHovenbitzer/graphify-al al-support@952d414) and
adapted to this fork's bcatlas_-prefixed tool names and multi-tenant
`_resolve_ctx` routing, plus the canonical `Type ID "Name"` object-label format
(bc-code-atlas spec 004-al-object-labels) -- fixture lookups below match by
node ID substring/attributes rather than upstream's bare-name label keys, so
they hold regardless of label format.

Covers three MCP-server behaviours:

1. ``bcatlas_resolve_node`` -- deterministic (object_type, object_name[, member])
   lookup over canonical node-ID segments, with exact member matches ranked
   before related ones (e.g. a trigger nested under the requested field).
2. ``bcatlas_get_node`` -- surfaces ambiguity (multiple ranked matches + a
   pointer to ``bcatlas_resolve_node``) instead of silently returning an
   arbitrary first hit.
3. ``bcatlas_get_neighbors`` -- qualifies bare member labels like
   ``.OnValidate()`` with their owner chain and appends ``[id: ...]`` to every
   line so results can be fed straight back into
   ``bcatlas_resolve_node``/``bcatlas_get_neighbors``.

Fixtures are fully synthetic AL (invented 50xxx objects), extracted with the
real AL extractor so node IDs/labels/relations match production graphs. The
tools are exercised end-to-end over the in-process Streamable HTTP transport
(same pattern as test_serve_http.py).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")
pytest.importorskip("mcp")
pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402

from graphify import serve as serve_mod  # noqa: E402
from graphify.extract import extract_al  # noqa: E402

_TABLE = """namespace Demo.Sales;

table 50100 "Widget Setup" {
    fields {
        field(1; "Calculation Type"; Integer) { trigger OnValidate() begin end; }
    }
}
"""

# The page carries a control with the SAME bare name as the table field
# (``Calculation Type``) -- the classic fuzzy-lookup decoy.
_PAGE = """namespace Demo.Sales;

page 50101 "Widget Setup Card" {
    SourceTable = "Widget Setup";
    layout { area(content) { field("Calculation Type"; Rec."Calculation Type") { } } }
}
"""

_INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}

_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


@pytest.fixture()
def graph(tmp_path: Path) -> dict:
    """Extract the synthetic AL objects into a servable graph.json.

    Returns the graph path plus the extractor-assigned node IDs so assertions
    never hardcode the ID scheme or the label format.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    for name, src in [("WidgetSetup.Table.al", _TABLE), ("WidgetSetupCard.Page.al", _PAGE)]:
        f = tmp_path / name
        f.write_text(src, encoding="utf-8")
        result = extract_al(f)
        nodes += result["nodes"]
        edges += result["edges"]
    data = {
        "directed": True,
        "nodes": [
            {"id": n["id"], "label": n["label"], "community": 0,
             "source_file": n.get("source_file", ""), "source_location": n.get("source_location", "")}
            for n in nodes
        ],
        "edges": [
            {"source": e["source"], "target": e["target"],
             "relation": e["relation"], "confidence": "EXTRACTED"}
            for e in edges
        ],
    }
    gp = tmp_path / "graph.json"
    gp.write_text(json.dumps(data), encoding="utf-8")

    def _calc_type(nodes_: list[dict]) -> list[dict]:
        return [n for n in nodes_ if str(n.get("label", "")).lower().endswith('"calculation type"')]

    table = next(n for n in nodes if str(n.get("label", "")).lower() == 'table 50100 "widget setup"'
                 or (not str(n.get("label", "")).startswith(".") and "widget setup" in str(n.get("label", "")).lower()
                     and "card" not in str(n.get("label", "")).lower()))
    field = next(n for n in _calc_type(nodes) if "_table_" in n["id"])
    control = next(n for n in _calc_type(nodes) if "_page_" in n["id"])
    (trigger,) = [n for n in nodes if str(n.get("label", "")) == ".OnValidate()"]
    return {
        "path": str(gp),
        "table": table["id"],
        "field": field["id"],
        "control": control["id"],
        "trigger": trigger["id"],
    }


def _client(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


def _session(client: TestClient) -> dict:
    init = client.post("/mcp", headers=_MCP_HEADERS, json=_INIT_BODY)
    assert init.status_code == 200
    session_id = init.headers.get("mcp-session-id")
    assert session_id
    headers = {**_MCP_HEADERS, "mcp-session-id": session_id}
    client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    return headers


def _call_tool(graph_path: str, name: str, arguments: dict) -> str:
    app = serve_mod._build_http_app(graph_path, json_response=True)
    with _client(app) as client:
        headers = _session(client)
        resp = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        return "\n".join(c["text"] for c in result["content"])


# --- tool registration -----------------------------------------------------

def test_resolve_node_listed_alongside_get_node(graph):
    """bcatlas_resolve_node is ADDED to the tool surface; bcatlas_get_node stays available."""
    app = serve_mod._build_http_app(graph["path"], json_response=True)
    with _client(app) as client:
        headers = _session(client)
        resp = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        names = {t["name"] for t in resp.json()["result"]["tools"]}
        assert {"bcatlas_resolve_node", "bcatlas_get_node", "bcatlas_get_neighbors",
                "bcatlas_query_graph"} <= names


# --- resolve_node ------------------------------------------------------------

def test_resolve_node_object(graph):
    out = _call_tool(graph["path"], "bcatlas_resolve_node",
                     {"object_type": "table", "object_name": "Widget Setup"})
    cards = out.split("\n\n")
    # Exact object match first, member note appended.
    assert f"  ID: {graph['table']}" in cards[0]
    assert "member nodes exist under this object" in out
    # The page never matches a table lookup.
    assert graph["control"] not in out


def test_resolve_node_member_exact_before_related(graph):
    """The field itself wins over its nested trigger; the same-named page
    control never appears (that is exactly the get_node fuzzy-match trap)."""
    out = _call_tool(graph["path"], "bcatlas_resolve_node",
                     {"object_type": "table", "object_name": "Widget Setup",
                      "member": "Calculation Type"})
    cards = out.split("\n\n")
    assert f"  ID: {graph['field']}" in cards[0]
    assert graph["trigger"] in out  # related match, listed after the exact one
    assert out.index(graph["field"]) < out.index(graph["trigger"])
    assert graph["control"] not in out


def test_resolve_node_member_trigger(graph):
    out = _call_tool(graph["path"], "bcatlas_resolve_node",
                     {"object_type": "table", "object_name": "Widget Setup",
                      "member": "OnValidate"})
    assert graph["trigger"] in out


def test_resolve_node_is_punctuation_insensitive(graph):
    out = _call_tool(graph["path"], "bcatlas_resolve_node",
                     {"object_type": "Table", "object_name": '"widget setup"'})
    assert f"  ID: {graph['table']}" in out


def test_resolve_node_miss_gives_guidance(graph):
    out = _call_tool(graph["path"], "bcatlas_resolve_node",
                     {"object_type": "table", "object_name": "No Such Object"})
    assert "No node found" in out
    assert "canonical ID" in out


# --- get_node ambiguity -------------------------------------------------------

def test_get_node_surfaces_ambiguity(graph):
    """Two nodes share the bare name "Calculation Type" (table field + page
    control) -- get_node must show both and point at resolve_node."""
    out = _call_tool(graph["path"], "bcatlas_get_node", {"label": "Calculation Type"})
    assert "resolve_node" in out
    assert graph["field"] in out
    assert graph["control"] in out


def test_get_node_unique_match_has_no_ambiguity_header(graph):
    out = _call_tool(graph["path"], "bcatlas_get_node", {"label": "Widget Setup Card"})
    assert "resolve_node" not in out
    assert f"  ID: {graph['table']}" not in out


# --- get_neighbors qualification ----------------------------------------------

def test_get_neighbors_qualifies_member_labels_and_shows_ids(graph):
    out = _call_tool(graph["path"], "bcatlas_get_neighbors", {"label": graph["field"]})
    # Header carries the resolved node's ID.
    assert out.splitlines()[0].endswith(f"[id: {graph['field']}]:")
    # The bare ".OnValidate()" trigger is qualified with its owner chain.
    assert ".OnValidate() of" in out
    # Every neighbor line carries the neighbor's node ID for follow-up calls.
    assert f"[id: {graph['trigger']}]" in out
    assert f"[id: {graph['table']}]" in out


def test_get_neighbors_object_label_not_dot_qualified(graph):
    out = _call_tool(graph["path"], "bcatlas_get_neighbors", {"label": graph["table"]})
    header = out.splitlines()[0]
    assert header.startswith("Neighbors of ") and " [id: " in header
    assert not header[len("Neighbors of "):].startswith(".")
