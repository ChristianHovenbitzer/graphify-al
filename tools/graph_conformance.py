#!/usr/bin/env python3
"""Deployment conformance check for a built AL graph.json.

Standalone script (no pytest): run it against the graph.json produced by a
full corpus build, after EVERY rebuild, before deploying the graph anywhere.
It guards against silent extractor regressions that unit tests cannot see
because they only ever run on tiny synthetic fixtures - e.g. a deployed graph
that shipped with ZERO `computes_from` edges although the extractor supports
them.

Checks (each violation is reported; any violation exits non-zero):

1. The graph has more than zero nodes and more than zero edges.
2. Every expected AL relation type is present with a count > 0:
   extends, binds, relates_to, computes_from, transfers_to, subscribes,
   calls, trigger, contains.
3. Every AL object node carries a `global_id` (the `al://...` federation key).
4. No self-loop edges (source == target).
5. No caption bleed-through: an object node must not share its `caption`
   with any member node it `contains` (the pre-#36 bug signature).

Usage:
    python tools/graph_conformance.py path/to/graph.json
    python tools/graph_conformance.py graph.json --require calls,contains

Exit codes: 0 = conformant, 1 = violations found, 2 = unreadable input.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXPECTED_RELATIONS = (
    "extends",
    "binds",
    "relates_to",
    "computes_from",
    "transfers_to",
    "subscribes",
    "calls",
    "trigger",
    "contains",
)


def _load(path: Path) -> tuple[list[dict], list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("graph file is not a JSON object")
    nodes = data.get("nodes", [])
    edges = data.get("links" if "links" in data else "edges", [])
    return list(nodes), list(edges)


def _is_al_object_node(node: dict) -> bool:
    """Heuristic for a real AL *object* node (table/page/codeunit/...).

    Object nodes live in a .al source file, are not the file node itself
    (label ends with `.al`) and not a member node (label starts with `.`).
    External stubs are checked too - they must carry a global_id as well.
    """
    if node.get("file_type") == "external":
        return True
    sf = str(node.get("source_file", ""))
    if not sf.lower().endswith(".al"):
        return False
    label = str(node.get("label", ""))
    return bool(label) and not label.endswith(".al") and not label.startswith(".")


def check(nodes: list[dict], edges: list[dict], required: tuple[str, ...]) -> list[str]:
    violations: list[str] = []

    if not nodes:
        violations.append("graph has 0 nodes")
    if not edges:
        violations.append("graph has 0 edges")

    counts: dict[str, int] = {}
    for e in edges:
        rel = str(e.get("relation", ""))
        counts[rel] = counts.get(rel, 0) + 1
    for rel in required:
        if counts.get(rel, 0) < 1:
            violations.append(f"expected relation '{rel}' has 0 edges")

    missing_gid = [
        str(n.get("label") or n.get("id"))
        for n in nodes
        if _is_al_object_node(n) and not n.get("global_id")
    ]
    if missing_gid:
        sample = ", ".join(missing_gid[:10])
        violations.append(
            f"{len(missing_gid)} object node(s) without global_id (e.g. {sample})"
        )

    self_loops = [e for e in edges if e.get("source") == e.get("target")]
    if self_loops:
        sample = ", ".join(
            f"{e.get('source')} [{e.get('relation')}]" for e in self_loops[:5]
        )
        violations.append(f"{len(self_loops)} self-loop edge(s) (e.g. {sample})")

    by_id = {n.get("id"): n for n in nodes}
    bleeds: list[str] = []
    for e in edges:
        if e.get("relation") != "contains":
            continue
        parent = by_id.get(e.get("source"))
        child = by_id.get(e.get("target"))
        if not parent or not child:
            continue
        cap = parent.get("caption")
        if cap and cap == child.get("caption"):
            bleeds.append(f"{parent.get('label')} <-> {child.get('label')} ({cap!r})")
    if bleeds:
        sample = "; ".join(bleeds[:5])
        violations.append(
            f"{len(bleeds)} caption bleed duplicate(s) between object and member "
            f"(e.g. {sample})"
        )

    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("graph", type=Path, help="path to a built graph.json")
    ap.add_argument(
        "--require",
        default=",".join(EXPECTED_RELATIONS),
        help="comma-separated relation types that must each have >0 edges",
    )
    args = ap.parse_args(argv)

    try:
        nodes, edges = _load(args.graph)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read graph file {args.graph}: {exc}", file=sys.stderr)
        return 2

    required = tuple(r.strip() for r in args.require.split(",") if r.strip())
    violations = check(nodes, edges, required)

    rel_counts: dict[str, int] = {}
    for e in edges:
        rel = str(e.get("relation", ""))
        rel_counts[rel] = rel_counts.get(rel, 0) + 1

    print(f"graph: {args.graph}")
    print(f"nodes: {len(nodes)}  edges: {len(edges)}")
    for rel in required:
        print(f"  {rel:<15} {rel_counts.get(rel, 0)}")

    if violations:
        print(f"\nNON-CONFORMANT: {len(violations)} violation(s)")
        for v in violations:
            print(f"  - {v}")
        return 1

    print("\nCONFORMANT: all deployment invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
