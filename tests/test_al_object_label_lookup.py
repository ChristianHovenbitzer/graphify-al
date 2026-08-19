"""Lookup behavior after AL object labels gained type/ID (spec 004-al-object-labels).

Covers US1 (a query using the natural, canonical AL reference now resolves;
a bare-name query still resolves too -- FR-004, no regression) and US2 (a
bare-name query matching multiple same-named objects of different types,
in different files, is reported as ambiguous via `find_node_ambiguity`,
rather than silently returning one arbitrary candidate -- FR-005).

Exercises `_find_node`/`find_node_ambiguity` directly (the shared matching
primitives behind `bcatlas_get_node`/`get_neighbors`/`get_signature`/
`get_procedure_body`/`get_object_source`) rather than the full MCP server,
since the tool functions are nested closures inside `serve()` with no
existing unit-test seam -- these are the actual load-bearing new behavior
(the cross-file ambiguity signal) and are directly importable.

Fixture files are deliberately named without the object's own name in the
filename (`ObjA.al`, not `Item.Table.al`) -- a bare-name query like "item"
already substring-matches a filename containing "item" (pre-existing
behavior, unrelated to this fix), which would otherwise confound these
tests with unrelated file-node matches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_al")

from graphify.build import build_from_json  # noqa: E402
from graphify.extract import extract  # noqa: E402
from graphify.serve import _find_node, find_node_ambiguity  # noqa: E402


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_full_canonical_reference_resolves(tmp_path: Path) -> None:
    p = _write(tmp_path, "ObjA.al", 'codeunit 12 "Gen. Jnl.-Post Line"\n{\n}\n')
    result = extract([p], cache_root=tmp_path / "cache")
    G = build_from_json(result)

    matches = _find_node(G, 'codeunit 12 "gen. jnl.-post line"')
    assert not find_node_ambiguity(G, 'codeunit 12 "gen. jnl.-post line"')
    assert G.nodes[matches[0]]["label"] == 'Codeunit 12 "Gen. Jnl.-Post Line"'


def test_bare_name_still_resolves_no_regression(tmp_path: Path) -> None:
    """FR-004 / US1 Acceptance Scenario 2: the bare name alone (the only
    format that worked before this fix) must keep working.
    """
    p = _write(tmp_path, "ObjA.al", 'codeunit 12 "Gen. Jnl.-Post Line"\n{\n}\n')
    result = extract([p], cache_root=tmp_path / "cache")
    G = build_from_json(result)

    matches = _find_node(G, "gen. jnl.-post line")
    assert len(matches) == 1
    assert G.nodes[matches[0]]["label"] == 'Codeunit 12 "Gen. Jnl.-Post Line"'


def test_same_name_different_type_disambiguated_by_full_reference(tmp_path: Path) -> None:
    """US2 Acceptance Scenario 1: a table and a page sharing a name are each
    individually retrievable by their own distinct full reference.
    """
    tbl = _write(tmp_path, "ObjA.al",
                 'table 27 "Item" { fields { field(1; "No."; Code[20]) { } } }\n')
    pg = _write(tmp_path, "ObjB.al", 'page 30 "Item" { layout { area(content) { } } }\n')
    result = extract([tbl, pg], cache_root=tmp_path / "cache")
    G = build_from_json(result)

    table_matches = _find_node(G, 'table 27 "item"')
    assert not find_node_ambiguity(G, 'table 27 "item"')
    assert G.nodes[table_matches[0]]["label"] == 'Table 27 "Item"'

    page_matches = _find_node(G, 'page 30 "item"')
    assert not find_node_ambiguity(G, 'page 30 "item"')
    assert G.nodes[page_matches[0]]["label"] == 'Page 30 "Item"'


def test_same_name_different_type_ambiguous_by_bare_name(tmp_path: Path) -> None:
    """US2 Acceptance Scenario 2: querying by the shared bare name alone must
    surface the ambiguity (find_node_ambiguity returning both rivals) rather
    than silently returning one arbitrarily-chosen candidate. Both objects
    now tie in the SUBSTRING tier (not exact) -- their full labels
    ("Table 27 \"Item\"", "Page 30 \"Item\"") no longer equal the bare query
    "item" now that labels carry type/ID tokens too (research.md R3) -- the
    ambiguity check must catch a tie in whichever tier actually wins, not
    only the exact tier. They also live in different files (ObjA.al/ObjB.al),
    which is what makes this genuinely ambiguous rather than ordinary
    same-file precedence.
    """
    tbl = _write(tmp_path, "ObjA.al",
                 'table 27 "Item" { fields { field(1; "No."; Code[20]) { } } }\n')
    pg = _write(tmp_path, "ObjB.al", 'page 30 "Item" { layout { area(content) { } } }\n')
    result = extract([tbl, pg], cache_root=tmp_path / "cache")
    G = build_from_json(result)

    rivals = find_node_ambiguity(G, "item")
    assert len(rivals) == 2, (
        'both Table 27 "Item" and Page 30 "Item" should tie in the winning tier'
    )
    labels = {G.nodes[nid]["label"] for nid in rivals}
    assert labels == {'Table 27 "Item"', 'Page 30 "Item"'}


def test_single_top_tier_match_is_not_ambiguous(tmp_path: Path) -> None:
    """A query whose winning tier has exactly one match is NOT ambiguous,
    even if other nodes appear afterward in a lower tier.
    """
    p = _write(tmp_path, "ObjA.al",
               'codeunit 12 "Gen. Jnl.-Post Line"\n{\n    procedure DoSomething()\n    begin\n    end;\n}\n')
    result = extract([p], cache_root=tmp_path / "cache")
    G = build_from_json(result)

    # Exact match on the full canonical reference; the procedure node
    # (".DoSomething()") doesn't overlap this query at all, so it's not
    # even a candidate, let alone a tie.
    matches = _find_node(G, 'codeunit 12 "gen. jnl.-post line"')
    assert len(matches) == 1
    assert not find_node_ambiguity(G, 'codeunit 12 "gen. jnl.-post line"')
