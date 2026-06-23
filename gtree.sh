#!/usr/bin/env bash
# gtree — convenience wrapper for the AL-aware graphify engine.
#
#   gtree <tree-name> <graphify-command> [args...]
#
# Resolves --graph to $GRAPHIFY_TREES/<tree-name>/graphify-out/graph.json.
# GRAPHIFY_TREES defaults to a sibling "graphify-trees" directory next to this repo,
# so analyzed codebases and their generated graphs live OUTSIDE this (public) repo.
#
# Examples:
#   gtree myrepo query "SomeCodeunit"
#   gtree myrepo affected "Some Setup Table"
#   gtree myrepo path "CodeunitA" "CodeunitB"
#   gtree myrepo explain "SomeCodeunit"
#
# Build/refresh a tree first (see AL_SUPPORT.md):
#   .venv/Scripts/python -m graphify update "$GRAPHIFY_TREES/myrepo"
set -euo pipefail
ENGINE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ENGINE/.venv/Scripts/python"
TREES="${GRAPHIFY_TREES:-$(cd "$ENGINE/.." && pwd)/graphify-trees}"
TREE="${1:?usage: gtree <tree-name> <command> [args...]}"; shift
GRAPH="$TREES/$TREE/graphify-out/graph.json"
[ -f "$GRAPH" ] || { echo "no tree at $GRAPH (build it first: graphify update \"$TREES/$TREE\")"; exit 1; }
exec "$PY" -m graphify "$@" --graph "$GRAPH"
