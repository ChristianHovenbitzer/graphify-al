# MCP stdio server - exposes graph query tools to Claude and other agents
from __future__ import annotations
import json
import math
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import NamedTuple
import networkx as nx
from networkx.readwrite import json_graph
from graphify.security import sanitize_label, check_graph_file_size_cap, validate_graph_path
from graphify.build import edge_data
from graphify.paths import default_graph_json as _default_graph_json
from graphify import source_lookup

try:
    import jieba as _jieba  # type: ignore[import-untyped]
except ImportError:
    _jieba = None


# --- multi-tenant routing (T029, specs/001-multi-version-serving in the
# parent bc-code-atlas repo) -------------------------------------------
#
# graphify itself is a general-purpose, corpus-agnostic graph server (see
# `_build_server`'s own docstring below) -- this fork
# (tools/graphify-al, submodule branch `bc-code-atlas-fixes`) adds an
# *opt-in* extra: every tool below accepts optional `country`/`version`
# arguments that route the call to a different, already-built graph
# instead of the one `graph_path` this server was started with. A caller
# that never passes them gets byte-identical behavior to before this
# change; only supplying both activates routing.


# Shared JSON-schema fragment merged into every bcatlas_* tool's
# inputSchema below -- one definition, ten call sites, so the wording
# never drifts between tools.
_ROUTING_SCHEMA_PROPERTIES: dict = {
    "country": {
        "type": "string",
        "description": (
            "Country code (e.g. 'w1', 'us') for a specific (country,"
            " version) pair's graph instead of this server's default."
            " Must be paired with `version` -- resolve both first (e.g."
            " via the registry server's discovery/resolve tools). Omit"
            " both to use the default graph."
        ),
    },
    "version": {
        "type": "string",
        "description": (
            "Exact resolved version string (e.g. 'w1-28.2.50931.52151')"
            " for a specific (country, version) pair's graph, paired with"
            " `country`. Omit both to use the default graph."
        ),
    },
}


class _RoutingError(Exception):
    """A country/version routing request that must surface as a clean
    tool-result error string to the caller, never a server crash or a
    raw traceback.
    """


class _RoutedGraph(NamedTuple):
    """Everything a tool handler needs for one specific graph -- either
    this server's own default (bound at startup) or a routed
    (country, version) pair loaded on demand.
    """

    G: nx.Graph
    communities: dict[int, list[str]]
    source_root: Path
    graph_path: str


# Bounded LRU cache of routed (country, version) graphs, on top of the one
# always-resident default graph (G/communities/_source_root in
# `_build_server`). 8 is a deliberately modest default: graph.json files
# are already bounded by `check_graph_file_size_cap`, but a handful of
# full networkx graphs held in memory simultaneously is still real
# resident memory for a long-running shared serving process. 8 comfortably
# covers "a few countries' current tips plus whatever a tester/agent is
# actively poking at right now" at this PoC's scale without growing
# unbounded -- tune via `_ROUTED_GRAPH_CACHE_SIZE` if that assumption
# proves wrong once real multi-version traffic is measured.
_ROUTED_GRAPH_CACHE_SIZE = 8
_routed_graph_cache: "OrderedDict[tuple[str, str], _RoutedGraph]" = OrderedDict()


def _bcatlas_data_root(explicit: str | None = None) -> Path:
    """Root under which multi-tenant warm (country, version) data lives.

    Mirrors `build/build/layout.py`'s `DEFAULT_DATA_DIR` convention
    (`<repo_root>/data`) *without importing it*: this fork has no
    dependency on the sibling `build/` project (which is being built in
    parallel by another agent as of this change, in the parent
    bc-code-atlas repo this submodule is vendored into), and the two
    conventions must stay in sync regardless of which project changes
    first. If a tiny shared "layout" package is ever extracted, this
    should import that instead of hand-duplicating the convention --
    tracked as a follow-up, not done here.

    Overridable via the `data_root` argument threaded down from
    `_main`'s `--data-root` flag / `GRAPHIFY_BCATLAS_DATA_ROOT` env var,
    for deployments where this submodule doesn't sit at the expected
    `tools/graphify-al` depth under the bc-code-atlas repo root.
    """
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("GRAPHIFY_BCATLAS_DATA_ROOT")
    if env:
        return Path(env).resolve()
    # tools/graphify-al/graphify/serve.py -> parents[3] is the repo root
    # (serve.py's dir -> graphify-al -> tools -> repo root).
    return Path(__file__).resolve().parents[3] / "data"


def _warm_graph_json_path(country: str, version: str, data_root: Path) -> Path:
    """Mirrors `build/build/layout.py`'s `warm_graph_dir(country, version)`
    convention (`data/warm/<country>/<version>/graph`), plus the
    `graph.json` filename this file always expects inside it.
    """
    return data_root / "warm" / country / version / "graph" / "graph.json"


def _warm_source_root(country: str, version: str, data_root: Path) -> Path:
    """Source root for a routed (country, version) pair's
    get_signature/get_procedure_body/get_object_source lookups.

    ASSUMPTION, documented rather than silently guessed: `layout.py` (as
    of this change) only defines `warm_search_dir`/`warm_graph_dir`, no
    dedicated "source" convention. cocoindex-code's "search" project
    directory is itself the raw AL/docs source tree it indexes in place
    -- this matches today's single-tenant deployment, where the search
    project_root (`data/`) literally contains `w1-28-src/` -- so it
    doubles as the routed graph's source_root here too. Reconcile with
    `build/`'s actual on-disk layout once it lands, if it turns out to
    define something more specific.
    """
    return data_root / "warm" / country / version / "search"


def _load_routed_graph(
    country: str, version: str, data_root_override: str | None
) -> _RoutedGraph:
    """Load (or return the cached) graph for one (country, version) pair.

    Historical (country, version) builds are immutable once promoted
    (constitution Principle III in the parent repo) -- unlike the
    default graph's `_maybe_reload()` mtime-watch, a cached routed entry
    is never invalidated by a background file change; the only way its
    cache entry goes away is LRU eviction (cheap and correct to re-load
    on the next request, since the source is immutable).
    """
    key = (country, version)
    cached = _routed_graph_cache.get(key)
    if cached is not None:
        _routed_graph_cache.move_to_end(key)
        return cached

    data_root = _bcatlas_data_root(data_root_override)
    graph_json_path = _warm_graph_json_path(country, version, data_root)
    if not graph_json_path.exists():
        raise _RoutingError(
            f"No warm graph data found for country={country!r} version={version!r}"
            f" -- expected a built graph at {graph_json_path}. This (country, version)"
            " pair has not been built yet. Request it via the build server's"
            " bcatlas_request_version tool (same country/version), then poll"
            " bcatlas_version_status until it reports ready before retrying this call."
        )
    try:
        # _load_graph calls sys.exit(1) on a missing/corrupted/oversized
        # file -- correct for this file's own CLI/startup entry points
        # (fail fast, don't serve a broken graph), but a single bad
        # routed request must never take down a shared multi-tenant
        # serving process for every other (country, version) pair. Same
        # technique `_maybe_reload()` already uses below for the same
        # reason.
        loaded_g = _load_graph(str(graph_json_path))
    except SystemExit:
        raise _RoutingError(
            f"Warm graph data for country={country!r} version={version!r} at"
            f" {graph_json_path} is missing or corrupted -- a rebuild may be"
            " required."
        )
    entry = _RoutedGraph(
        G=loaded_g,
        communities=_communities_from_graph(loaded_g),
        source_root=_warm_source_root(country, version, data_root),
        graph_path=str(graph_json_path),
    )
    _routed_graph_cache[key] = entry
    _routed_graph_cache.move_to_end(key)
    while len(_routed_graph_cache) > _ROUTED_GRAPH_CACHE_SIZE:
        _routed_graph_cache.popitem(last=False)
    return entry


def _load_graph(graph_path: str) -> nx.Graph:
    try:
        resolved = Path(graph_path).resolve()
        if resolved.suffix != ".json":
            raise ValueError(f"Graph path must be a .json file, got: {graph_path!r}")
        if not resolved.exists():
            raise FileNotFoundError(f"Graph file not found: {resolved}")
        check_graph_file_size_cap(resolved)
        safe = resolved
        data = json.loads(safe.read_text(encoding="utf-8"))
        if "links" not in data and "edges" in data:
            data = dict(data, links=data["edges"])
        data = {**data, "directed": True}
        try:
            return json_graph.node_link_graph(data, edges="links")
        except TypeError:
            return json_graph.node_link_graph(data)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"error: graph.json is corrupted ({exc}). Re-run /graphify to rebuild.", file=sys.stderr)
        sys.exit(1)


def _communities_from_graph(G: nx.Graph) -> dict[int, list[str]]:
    """Reconstruct community dict from community property stored on nodes."""
    communities: dict[int, list[str]] = {}
    for node_id, data in G.nodes(data=True):
        cid = data.get("community")
        if cid is not None:
            communities.setdefault(int(cid), []).append(node_id)
    return communities


def _strip_diacritics(text: str | None) -> str:
    import unicodedata
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _search_tokens(text: str) -> list[str]:
    """Split text into word tokens, stripping punctuation and diacritics."""
    return re.findall(r"\w+", _strip_diacritics(str(text)).lower())


def _has_chinese(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _segment_chinese(text: str) -> list[str]:
    """Segment Chinese text and keep the original term for exact matching."""
    if _jieba is not None:
        segments = [w for w in _jieba.cut(text) if len(w.strip()) > 0]
    else:
        segments = [text[i:i + 2] for i in range(len(text) - 1)] or [text]
    if len(text) > 1 and text not in segments:
        segments.append(text)
    return segments


# English interrogative/functional words that legitimately appear in a
# natural-language question but are noise (and occasionally dangerous) as
# a symbol-search term. Concrete repro against the real w1-28-src graph:
# "what subscribes to X" / "what calls X" tokenized "what" as a search
# term, which hit the full-query exact-match tier in _score_nodes against
# a real node literally labeled "What has been done" (a Translation
# README heading), silently seeding the BFS on a bogus, unrelated root
# with no error -- reproduced twice with different questions, same bogus
# node both times. Bug found against real project data
# (constitution Principle VI); fork branch bc-code-atlas-fixes.
_QUESTION_STOPWORDS = frozenset({
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "does", "do", "did", "is", "are", "was", "were", "be", "been", "being",
    "the", "a", "an", "of", "to", "in", "on", "for", "with", "and", "or",
    "this", "that", "these", "those", "can", "could", "would", "should",
    "will", "shall", "please", "tell", "me", "show",
})


def _is_searchable(term: str) -> bool:
    """True if term is Chinese, non-English, or an English word longer than 2 chars."""
    if all("a" <= ch <= "z" for ch in term):
        return len(term) > 2 and term not in _QUESTION_STOPWORDS
    return True


def _query_terms(question: str) -> list[str]:
    """Split a query into searchable terms, segmenting Chinese text."""
    terms: list[str] = []
    for raw in question.split():
        if _has_chinese(raw):
            for seg in _segment_chinese(raw.lower().strip()):
                seg = seg.strip()
                if seg and _is_searchable(seg):
                    terms.append(seg)
        else:
            # Strip punctuation without touching Unicode characters (avoid NFKD mangling non-Latin scripts)
            for tok in re.findall(r"\w+", raw.lower()):
                if _is_searchable(tok):
                    terms.append(tok)
    return terms


_EXACT_MATCH_BONUS = 1000.0
_PREFIX_MATCH_BONUS = 100.0
_SUBSTRING_MATCH_BONUS = 1.0
_SOURCE_MATCH_BONUS = 0.5


def _compute_idf(G: nx.Graph, terms: list[str]) -> dict[str, float]:
    """IDF weights for query terms, cached in G.graph['_idf_cache'].

    Common terms like 'error' or 'exception' that match hundreds of nodes get
    low weights; rare identifiers like 'FooBarService' get high weights.
    Cache is stored on the graph object itself so it auto-invalidates when
    _maybe_reload() replaces G with a new object.
    """
    cache: dict[str, float] = G.graph.setdefault("_idf_cache", {})
    N = G.number_of_nodes() or 1
    uncached = [t for t in terms if t not in cache]
    if uncached:
        df: dict[str, int] = {t: 0 for t in uncached}
        for _, data in G.nodes(data=True):
            norm_label = (
                data.get("norm_label") or _strip_diacritics(data.get("label") or "")
            ).lower()
            for t in uncached:
                if t in norm_label:
                    df[t] += 1
        for t in uncached:
            cache[t] = math.log(1 + N / (1 + df[t]))
    return {t: cache.get(t, math.log(1 + N)) for t in terms}


def _score_nodes(G: nx.Graph, terms: list[str]) -> list[tuple[float, str]]:
    scored = []
    norm_terms = [tok for t in terms for tok in _search_tokens(t)]
    idf = _compute_idf(G, norm_terms)
    # Whole-query string for full-label matching (mirrors _find_node's `term`).
    joined = " ".join(norm_terms)
    # Weight the full-query bonus by the rarest constituent term so a specific
    # multi-word label still outweighs common-token noise; floor at 1.0.
    joined_w = max((idf.get(t, 1.0) for t in norm_terms), default=1.0)
    for nid, data in G.nodes(data=True):
        norm_label = data.get("norm_label") or _strip_diacritics(data.get("label") or "").lower()
        bare_label = norm_label.rstrip("()")
        # Tokenized form of the label (punctuation stripped, same transform as the
        # query). norm_label may still carry punctuation like ':' or '-', which a
        # tokenized query can never equal; comparing token-joined forms on both
        # sides makes "uoce: dehumidifier driver" match query "uoce dehumidifier
        # driver".
        label_tokens = " ".join(_search_tokens(data.get("label") or ""))
        source = (data.get("source_file") or "").lower()
        score = 0.0
        # Full-query tier: a multi-word query that equals (or prefixes) the whole
        # label must dominate the per-token bag-of-words sums below, so `path`/
        # `query` resolve the same node `explain` does (via _find_node). Without
        # this, no single token equals a multi-word label, the per-token exact
        # tier never fires, and every node sharing the token set ties -> arbitrary
        # node-id sort -> wrong/disconnected endpoint -> false "No path found".
        if joined:
            nid_lower = nid.lower()
            if joined in (norm_label, bare_label, label_tokens, nid_lower):
                score += _EXACT_MATCH_BONUS * 10 * joined_w
            elif (
                norm_label.startswith(joined)
                or bare_label.startswith(joined)
                or label_tokens.startswith(joined)
            ):
                score += _PREFIX_MATCH_BONUS * 10 * joined_w
        for t in norm_terms:
            w = idf.get(t, 1.0)
            # Three-tier precedence: exact > prefix > substring (take the
            # strongest tier per term so a single term cannot double-count).
            if t == norm_label or t == bare_label:
                score += _EXACT_MATCH_BONUS * w
            elif norm_label.startswith(t) or bare_label.startswith(t):
                score += _PREFIX_MATCH_BONUS * w
            elif t in norm_label:
                score += _SUBSTRING_MATCH_BONUS * w
            if t in source:
                score += _SOURCE_MATCH_BONUS * w
        if score > 0:
            scored.append((score, nid))
    # Sort by score desc; break ties toward the shorter label so a concise exact
    # match beats a longer superset that happens to share the same score.
    scored.sort(key=lambda s: (-s[0], len(G.nodes[s[1]].get("label") or s[1]), s[1]))
    return scored


def _pick_seeds(scored: list[tuple[float, str]], max_k: int = 3, gap_ratio: float = 0.2) -> list[str]:
    """Select BFS seed nodes, stopping when score drops too far below the top.

    Prevents high-frequency noise terms (error, exception) from stealing seed
    slots from a dominant identifier match. When FooBarService scores 1000 and
    error nodes score 1.0, only FooBarService is seeded — the score gap is 99.9%
    which is well above the 20% threshold that would allow additional seeds.
    """
    if not scored:
        return []
    top_score = scored[0][0]
    seeds = []
    for score, nid in scored[:max_k]:
        if seeds and score < top_score * gap_ratio:
            break
        seeds.append(nid)
    return seeds


_CONTEXT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("call", ("call", "calls", "called", "invoke", "invokes", "invoked")),
    ("import", ("import", "imports", "imported", "module", "modules")),
    ("field", ("field", "fields", "member", "members", "property", "properties")),
    ("parameter_type", ("parameter", "parameters", "param", "params", "argument", "arguments")),
    ("return_type", ("return", "returns", "returned")),
    ("generic_arg", ("generic", "generics", "template", "templates")),
)


_CONTEXT_FILTER_ALIASES: dict[str, str] = {
    "param": "parameter_type",
    "params": "parameter_type",
    "parameter": "parameter_type",
    "parameters": "parameter_type",
    "argument": "parameter_type",
    "arguments": "parameter_type",
    "arg": "parameter_type",
    "args": "parameter_type",
    "return": "return_type",
    "returns": "return_type",
    "returned": "return_type",
    "generic": "generic_arg",
    "generics": "generic_arg",
    "template": "generic_arg",
    "templates": "generic_arg",
    "annotation": "attribute",
    "annotations": "attribute",
    "decorator": "attribute",
    "decorators": "attribute",
    "calls": "call",
    "called": "call",
    "invoke": "call",
    "invocation": "call",
    "fields": "field",
    "property": "field",
    "properties": "field",
    "member": "field",
    "members": "field",
    "imports": "import",
    "imported": "import",
    "module": "import",
    "modules": "import",
    "exports": "export",
    "exported": "export",
}


def _normalize_context_filters(filters: list[str] | None) -> list[str]:
    if not filters:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in filters:
        key = _strip_diacritics(str(value)).strip().lower()
        if not key:
            continue
        key = _CONTEXT_FILTER_ALIASES.get(key, key)
        if key not in seen:
            seen.add(key)
            normalized.append(key)
    return normalized


def _infer_context_filters(question: str) -> list[str]:
    lowered = {
        _strip_diacritics(token).lower()
        for token in question.replace("?", " ").replace(",", " ").split()
    }
    inferred: list[str] = []
    for context, hints in _CONTEXT_HINTS:
        if any(hint in lowered for hint in hints):
            inferred.append(context)
    return inferred


def _resolve_context_filters(question: str, explicit_filters: list[str] | None = None) -> tuple[list[str], str | None]:
    normalized = _normalize_context_filters(explicit_filters)
    if normalized:
        return normalized, "explicit"
    inferred = _infer_context_filters(question)
    if inferred:
        return inferred, "heuristic"
    return [], None


def _crosses_app_boundary(G: nx.Graph, u: str, v: str) -> bool:
    """True if `u` and `v` have different, both-known `al_owning_app` tags
    (bc-code-atlas #27). Neither missing-tag node (non-AL corpus, or a file
    with no discoverable app.json) counts as crossing anything -- an unknown
    boundary is not a confirmed one."""
    au = G.nodes[u].get("al_owning_app")
    av = G.nodes[v].get("al_owning_app")
    return bool(au) and bool(av) and au != av


def _filter_graph_by_context(G: nx.Graph, context_filters: list[str] | None) -> nx.Graph:
    filters = set(_normalize_context_filters(context_filters))
    if not filters:
        return G
    # "cross_app" is a structural predicate (owning-app mismatch across the
    # edge's two endpoints), not an edge `context`/relation-kind value like
    # the rest of `filters` -- so it's split out and ANDed against whatever
    # relation-kind filters remain, rather than joining the context
    # whitelist it can never literally match (bc-code-atlas #27).
    cross_app_only = "cross_app" in filters
    relation_filters = filters - {"cross_app"}
    H = G.__class__()
    H.add_nodes_from(G.nodes(data=True))

    def _keep(u: str, v: str, data: dict) -> bool:
        if relation_filters and data.get("context") not in relation_filters:
            return False
        if cross_app_only and not _crosses_app_boundary(G, u, v):
            return False
        return True

    if isinstance(G, (nx.MultiGraph, nx.MultiDiGraph)):
        for u, v, key, data in G.edges(keys=True, data=True):
            if _keep(u, v, data):
                H.add_edge(u, v, key=key, **data)
    else:
        for u, v, data in G.edges(data=True):
            if _keep(u, v, data):
                H.add_edge(u, v, **data)
    return H


def _hop_distances(seeds: list[str], edges: list[tuple]) -> dict[str, int]:
    """Reconstruct each node's hop-distance from the nearest seed from an edge list.

    Both _bfs and _dfs append an edge (u, v) to their edges_seen list only once
    u itself has already been popped/expanded, which is always after u's own
    distance has been resolved (as a seed, or via an earlier edge in the same
    list). So a single forward pass over edges in emission order is sufficient
    -- for _bfs this recovers the exact BFS shortest-hop distance; for _dfs it
    recovers distance along that specific traversal path.
    """
    dist = {s: 0 for s in seeds}
    for u, v in edges:
        if v not in dist and u in dist:
            dist[v] = dist[u] + 1
    return dist


def _all_neighbors(G: nx.Graph, n: str) -> set[str]:
    """Neighbors of n, following edges in either direction on a directed graph.

    Production graphs always load as directed (see _load_graph), and most
    references/table_relation/calls edges point FROM a consumer TO the
    concept it depends on (e.g. GenJnlPostLine --references--> Customer) --
    not the other way. A BFS/DFS starting at Customer that only follows
    G.neighbors() (successors on a DiGraph) would never reach GenJnlPostLine
    even though it's one hop away, because the edge points the "wrong" way
    for outward-only traversal. shortest_path already works around this with
    G.to_undirected(); _bfs/_dfs need the same treatment to explore "what
    references this concept", not just "what this concept points to".
    """
    if G.is_directed():
        return set(G.successors(n)) | set(G.predecessors(n))
    return set(G.neighbors(n))


def _bfs(G: nx.Graph, start_nodes: list[str], depth: int) -> tuple[set[str], list[tuple]]:
    # Compute hub threshold: nodes above this degree are not expanded as transit.
    # p99 of degree distribution, floored at 50 to avoid over-blocking small graphs.
    degrees = [G.degree(n) for n in G.nodes()]
    if degrees:
        degrees_sorted = sorted(degrees)
        p99_idx = int(len(degrees_sorted) * 0.99)
        hub_threshold = max(50, degrees_sorted[p99_idx])
    else:
        hub_threshold = 50
    seed_set = set(start_nodes)
    visited: set[str] = set(start_nodes)
    frontier = set(start_nodes)
    edges_seen: list[tuple] = []
    for _ in range(depth):
        next_frontier: set[str] = set()
        for n in frontier:
            # Don't expand through high-degree hubs (except seeds - a hub that
            # is the starting node should still be explored).
            if n not in seed_set and G.degree(n) >= hub_threshold:
                continue
            for neighbor in _all_neighbors(G, n):
                if neighbor not in visited:
                    next_frontier.add(neighbor)
                    edges_seen.append((n, neighbor))
        visited.update(next_frontier)
        frontier = next_frontier
    return visited, edges_seen


def _dfs(G: nx.Graph, start_nodes: list[str], depth: int) -> tuple[set[str], list[tuple]]:
    degrees = [G.degree(n) for n in G.nodes()]
    if degrees:
        degrees_sorted = sorted(degrees)
        p99_idx = int(len(degrees_sorted) * 0.99)
        hub_threshold = max(50, degrees_sorted[p99_idx])
    else:
        hub_threshold = 50
    seed_set = set(start_nodes)
    visited: set[str] = set()
    edges_seen: list[tuple] = []
    stack = [(n, 0) for n in reversed(start_nodes)]
    while stack:
        node, d = stack.pop()
        if node in visited or d > depth:
            continue
        visited.add(node)
        if node not in seed_set and G.degree(node) >= hub_threshold:
            continue
        for neighbor in _all_neighbors(G, node):
            if neighbor not in visited:
                stack.append((neighbor, d + 1))
                edges_seen.append((node, neighbor))
    return visited, edges_seen


def _subgraph_to_text(
    G: nx.Graph,
    nodes: set[str],
    edges: list[tuple],
    token_budget: int = 6000,
    *,
    seeds: list[str] | None = None,
    distances: dict[str, int] | None = None,
    scores: dict[str, float] | None = None,
) -> str:
    """Render subgraph as text, cutting at token_budget (approx 3 chars/token).

    seeds: exact-match nodes rendered first before the ranked expansion, so
    the queried symbol always appears at the top of the output.

    distances/scores: optional hop-distance-from-seed and query-relevance
    maps (see _hop_distances / _score_nodes). Without them every node ties on
    both keys and the sort degrades to the previous degree-only ordering --
    with them, a node that's one hop from the seed and actually matches the
    query outranks an unrelated hub node with a higher raw degree, which pure
    degree-sorting got backwards (a directly relevant answer could be buried
    behind hundreds of high-connectivity nodes and cut off by token_budget
    before ever being rendered).
    """
    char_budget = token_budget * 3
    lines = []
    seed_set = set(seeds or [])
    distances = distances or {}
    scores = scores or {}
    far = len(G) + 1

    def _rank_key(n: str) -> tuple:
        return (distances.get(n, far), -scores.get(n, 0.0), -G.degree(n))

    ordered = [n for n in (seeds or []) if n in nodes] + \
              sorted(nodes - seed_set, key=_rank_key)
    for nid in ordered:
        d = G.nodes[nid]
        # Every LLM-derived field passes through sanitize_label before being
        # concatenated into MCP tool output (F-010): an attacker who controls a
        # corpus document can otherwise inject ANSI escapes, fake graphify-out
        # log lines, or prompt-injection markup into the model's context via
        # source_file / source_location / community.
        line = (
            f"NODE {sanitize_label(d.get('label', nid))} "
            f"[src={sanitize_label(str(d.get('source_file', '')))} "
            f"loc={sanitize_label(str(d.get('source_location', '')))} "
            f"community={sanitize_label(str(d.get('community_name') or d.get('community', '')))}]"
        )
        lines.append(line)
    for u, v in edges:
        if u in nodes and v in nodes:
            # _bfs/_dfs now traverse edges in either direction (_all_neighbors),
            # so the (u, v) discovery order doesn't necessarily match the real
            # edge direction on a directed graph -- look up whichever direction
            # actually exists and render using the graph's real source/target,
            # not the traversal order, so e.g. a `references` edge doesn't get
            # printed backwards.
            if G.has_edge(u, v):
                src_id, tgt_id = u, v
            elif G.has_edge(v, u):
                src_id, tgt_id = v, u
            else:
                continue
            raw = G[src_id][tgt_id]
            d = next(iter(raw.values()), {}) if isinstance(G, (nx.MultiGraph, nx.MultiDiGraph)) else raw
            context = d.get("context")
            context_suffix = f" context={sanitize_label(str(context))}" if context else ""
            line = (
                f"EDGE {sanitize_label(G.nodes[src_id].get('label', src_id))} "
                f"--{sanitize_label(str(d.get('relation', '')))} "
                f"[{sanitize_label(str(d.get('confidence', '')))}{context_suffix}]--> "
                f"{sanitize_label(G.nodes[tgt_id].get('label', tgt_id))}"
            )
            lines.append(line)
    output = "\n".join(lines)
    if len(output) > char_budget:
        cut_at = output[:char_budget].rfind("\n")
        cut_at = cut_at if cut_at > 0 else char_budget
        total_nodes = sum(1 for l in lines if l.startswith("NODE "))
        shown_nodes = output[:cut_at].count("\nNODE ") + (1 if output.startswith("NODE ") else 0)
        cut_count = total_nodes - shown_nodes
        output = (
            output[:cut_at]
            + f"\n... (truncated — {cut_count} more nodes cut by ~{token_budget}-token budget."
            f" Narrow with context_filter=['call'] or use get_node for a specific symbol)"
        )
    return output


def _query_graph_text(
    G: nx.Graph,
    question: str,
    *,
    mode: str = "bfs",
    depth: int = 3,
    token_budget: int = 6000,
    context_filters: list[str] | None = None,
) -> str:
    terms = _query_terms(question)
    scored = _score_nodes(G, terms)
    start_nodes = _pick_seeds(scored)
    if not start_nodes:
        return "No matching nodes found."
    resolved_filters, filter_source = _resolve_context_filters(question, context_filters)
    traversal_graph = _filter_graph_by_context(G, resolved_filters)
    nodes, edges = _dfs(traversal_graph, start_nodes, depth) if mode == "dfs" else _bfs(traversal_graph, start_nodes, depth)
    header_parts = [
        f"Traversal: {mode.upper()} depth={depth}",
        f"Start: {[G.nodes[n].get('label', n) for n in start_nodes]}",
    ]
    if resolved_filters:
        header_parts.append(f"Context: {', '.join(resolved_filters)} ({filter_source})")
    header_parts.append(f"{len(nodes)} nodes found")
    header = " | ".join(header_parts) + "\n\n"
    distances = _hop_distances(start_nodes, edges)
    score_map = {nid: score for score, nid in _rank_scores(G, terms, start_nodes, scored)}
    return header + _subgraph_to_text(
        traversal_graph, nodes, edges, token_budget,
        seeds=start_nodes, distances=distances, scores=score_map,
    )


def _rank_scores(
    G: nx.Graph, terms: list[str], start_nodes: list[str], seed_scored: list[tuple[float, str]]
) -> list[tuple[float, str]]:
    """Re-score nodes for ranking, dropping query terms every seed already satisfies.

    A term present in every picked seed's own label adds zero discriminating
    signal among that seed's neighbors -- they're all tautologically "about"
    the seed by definition of being connected to it -- but it still inflates
    the score of an unrelated same-file sibling over the actually-relevant
    cross-object answer (e.g. querying "customer posting logic" from seed
    `Customer`: every 1-hop neighbor trivially matches "customer", so an
    irrelevant `Customer.CreateAndShowNewInvoice()` outscores the real
    `Gen. Jnl.-Post Line` on that term alone, even though "posting"/"logic"
    are what should differentiate them). Falls back to the seed-picking score
    if every term is seed-satisfied (no better signal available).
    """
    seed_labels = [
        (G.nodes[s].get("norm_label") or _strip_diacritics(G.nodes[s].get("label") or "")).lower()
        for s in start_nodes
    ]
    ranking_terms = [t for t in terms if not all(t.lower() in sl for sl in seed_labels)]
    if not ranking_terms or not seed_labels:
        return seed_scored
    return _score_nodes(G, ranking_terms)


class _NodeMatches(list):
    """A `list[str]` (every existing caller's `matches[0]`/`if not matches`/
    iteration keeps working unchanged) that additionally exposes how many
    of the leading entries tied for the BEST match tier, via
    `.top_tier_count`.

    Needed for FR-005 (spec 004-al-object-labels): a query matching more
    than one node in its best-matching tier is genuinely ambiguous (e.g. a
    table and a page both named "Item") and callers that care should say
    so, rather than silently taking `matches[0]`. This must be the *best*
    tier, not always the exact tier specifically: since object labels now
    carry type/ID tokens (spec 004), a bare-name query like "item" no
    longer lands in the exact tier for either candidate (their full labels
    are "Table 27 \"Item\"" / "Page 30 \"Item\"") -- both instead tie in the
    substring tier, which is exactly where this ambiguity must still be
    caught. A single top-tier match with additional looser matches behind
    it in a lower tier is NOT ambiguous -- `top_tier_count` only counts
    ties within whichever tier is actually winning.
    """

    top_tier_count: int = 0


def _find_node(G: nx.Graph, label: str) -> _NodeMatches:
    """Return node IDs whose label or ID matches the search term (diacritic-insensitive).

    Results are ordered by three-tier precedence: exact match, then prefix match,
    then substring match. Node-ID exact matches are grouped with label exact matches.
    """
    term = " ".join(_search_tokens(label))
    if not term:
        return _NodeMatches()
    exact: list[str] = []
    prefix: list[str] = []
    substring: list[str] = []
    for nid, d in G.nodes(data=True):
        norm_label = d.get("norm_label") or _strip_diacritics(d.get("label") or "").lower()
        bare_label = norm_label.rstrip("()")
        label_tokens = " ".join(_search_tokens(d.get("label") or ""))
        nid_lower = nid.lower()
        if term == norm_label or term == bare_label or term == label_tokens or term == nid_lower:
            exact.append(nid)
        elif (
            norm_label.startswith(term)
            or bare_label.startswith(term)
            or label_tokens.startswith(term)
            or nid_lower.startswith(term)
        ):
            prefix.append(nid)
        elif term in norm_label or term in label_tokens:
            substring.append(nid)
    result = _NodeMatches(exact + prefix + substring)
    result.top_tier_count = len(exact) or len(prefix) or len(substring)
    return result


_GLOBAL_ID_RE = re.compile(r"^al://(?P<q>[^/]*)/(?P<t>[^/]*)/(?P<n>.*)$")


def _al_global_id_type_name(global_id: str) -> tuple[str, str] | None:
    """(type, name) parsed out of a `global_id`, ignoring its qualifier."""
    m = _GLOBAL_ID_RE.match(global_id or "")
    return (m.group("t"), m.group("n")) if m else None


def _find_node_by_global_id(G: nx.Graph, global_id: str) -> list[str]:
    """Node IDs matching `global_id` (bc-code-atlas #26).

    `global_id` (`al://<qualifier>/<type>/<name>`) is a deterministic join key
    computed so a stub in one corpus and the real object in another carry the
    same value -- this is the resolver half of that: given a `global_id`
    copied from one graph, find the matching node(s) on this one, for
    cross-graph federation at query time.

    Tries an exact match first. #31: two independently-built graphs can still
    disagree on the qualifier for the same real object -- the referencing app
    never imported the target's own `namespace` declaration, so its external
    stub's `global_id` carries an empty qualifier while the object's own
    corpus resolves a real one. When no exact match is found, fall back to
    matching on type+name alone (safe in practice -- BC enforces object-name
    uniqueness per type), so a caller handing over an unqualified/mismatched
    `global_id` still gets a candidate instead of a hard miss.
    """
    term = (global_id or "").strip()
    if not term:
        return []
    exact = [nid for nid, d in G.nodes(data=True) if d.get("global_id") == term]
    if exact:
        return exact
    parsed = _al_global_id_type_name(term)
    if not parsed:
        return []
    return [
        nid for nid, d in G.nodes(data=True)
        if _al_global_id_type_name(str(d.get("global_id", ""))) == parsed
    ]


def _filter_blank_stdin() -> None:
    """Filter blank lines from stdin before MCP reads it.

    Some MCP clients (Claude Desktop, etc.) send blank lines between JSON
    messages. The MCP stdio transport tries to parse every line as a
    JSONRPCMessage, so a bare newline triggers a Pydantic ValidationError.
    This installs an OS-level pipe that relays stdin while dropping blanks.
    """
    import os
    import threading

    r_fd, w_fd = os.pipe()
    saved_fd = os.dup(sys.stdin.fileno())

    def _relay() -> None:
        try:
            with open(saved_fd, "rb") as src, open(w_fd, "wb") as dst:
                for line in src:
                    if line.strip():
                        dst.write(line)
                        dst.flush()
        except Exception:
            pass

    threading.Thread(target=_relay, daemon=True).start()
    os.dup2(r_fd, sys.stdin.fileno())
    os.close(r_fd)
    sys.stdin = open(0, "r", closefd=False)


def _build_server(
    graph_path: str,
    *,
    instructions: str | None = None,
    source_root: str | None = None,
    data_root: str | None = None,
):
    """Build the configured low-level MCP Server (shared by every transport).

    All graph query tools and resources are registered here over a single
    ``mcp.server.Server`` instance; the caller picks the transport (stdio or
    Streamable HTTP) and runs it. Hot-reload of graph.json works the same way
    regardless of transport, since reloads happen inside the tool handlers.

    ``instructions`` is deliberately not domain-specific here -- graphify is a
    general-purpose graph server. Callers serving a particular corpus (e.g. a
    specific codebase) should pass their own description via this parameter
    (or ``--instructions``/``GRAPHIFY_INSTRUCTIONS`` on the CLI) so an agent
    with zero prior context knows what graph it's actually looking at.

    ``source_root`` anchors bcatlas_get_signature/bcatlas_get_procedure_body/bcatlas_get_object_source,
    which re-read the on-disk source a node points at (the graph itself only
    stores file+line, not text). Defaults to ``graph_path``'s grandparent
    (``<source_root>/<GRAPHIFY_OUT>/graph.json``), which matches every
    existing deployment layout without needing an extra flag.

    ``data_root`` (multi-tenant routing, T029) is the root under which
    routed ``country``/``version`` tool arguments are resolved to a
    different graph.json -- see ``_bcatlas_data_root``. Only consulted
    when a tool call actually supplies both ``country`` and ``version``;
    every tool's zero-argument behavior is unchanged and still serves the
    one ``graph_path``/``source_root`` this function was called with.
    """
    import threading

    _source_root = Path(source_root).resolve() if source_root else Path(graph_path).resolve().parent.parent

    try:
        from mcp.server import Server
        from mcp import types
        from mcp.types import AnyUrl
    except ImportError as e:
        raise ImportError('mcp not installed. Run: pip install "graphifyy[mcp]"') from e

    G = _load_graph(graph_path)
    communities = _communities_from_graph(G)

    # Hot-reload state: mtime+size key lets us detect graph.json changes without
    # polling. Initialised from the file stat at startup so the first tool call
    # never triggers a redundant reload.
    _reload_lock = threading.Lock()
    try:
        _s = Path(graph_path).stat()
        _reload_state: dict = {"mtime_ns": _s.st_mtime_ns, "size": _s.st_size}
    except FileNotFoundError:
        _reload_state = {"mtime_ns": 0, "size": -1}

    def _maybe_reload() -> None:
        nonlocal G, communities
        try:
            s = Path(graph_path).stat()
            key = (s.st_mtime_ns, s.st_size)
        except FileNotFoundError:
            return
        if key == (_reload_state["mtime_ns"], _reload_state["size"]):
            return
        with _reload_lock:
            try:
                s = Path(graph_path).stat()
                key = (s.st_mtime_ns, s.st_size)
            except FileNotFoundError:
                return
            if key == (_reload_state["mtime_ns"], _reload_state["size"]):
                return  # another thread already reloaded
            try:
                new_G = _load_graph(graph_path)
            except SystemExit:
                return  # keep serving stale graph on transient read error
            G = new_G
            communities = _communities_from_graph(new_G)
            _reload_state["mtime_ns"], _reload_state["size"] = key

    def _resolve_ctx(arguments: dict) -> _RoutedGraph:
        """The graph context for one tool call (T029).

        Neither ``country`` nor ``version`` supplied -> this server's own
        default graph, unchanged from before this change (the dispatcher
        below already calls ``_maybe_reload()`` ahead of every handler, so
        the default branch here doesn't need to call it again). Both
        supplied -> that specific routed (country, version) pair's graph
        (cached; see ``_load_routed_graph``). Exactly one supplied is a
        caller error, not a guess.
        """
        country = arguments.get("country")
        version = arguments.get("version")
        if country is None and version is None:
            return _RoutedGraph(
                G=G, communities=communities, source_root=_source_root, graph_path=graph_path
            )
        if not country or not version:
            raise _RoutingError(
                "Both `country` and `version` must be supplied together to"
                " query a specific (country, version) pair. Resolve an exact"
                " version first (e.g. via the registry server's"
                " bcatlas_resolve_version tool), then pass both."
            )
        return _load_routed_graph(country, version, data_root)

    server = Server("graphify", instructions=instructions)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="bcatlas_query_graph",
                description="Search the knowledge graph using BFS or DFS. Returns relevant nodes and edges as text context.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "Natural language question or keyword search"},
                        "mode": {"type": "string", "enum": ["bfs", "dfs"], "default": "bfs",
                                 "description": "bfs=broad context, dfs=trace a specific path"},
                        "depth": {"type": "integer", "default": 3, "description": "Traversal depth (1-6)"},
                        "token_budget": {"type": "integer", "default": 6000, "description": "Max output tokens"},
                        "context_filter": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional explicit edge-context filter, e.g."
                                " ['call', 'field']. Also accepts 'cross_app',"
                                " a structural filter (not a relation kind) that"
                                " keeps only edges whose two endpoints belong to"
                                " different apps (AL corpora only) -- combine it"
                                " with a relation kind (e.g. ['cross_app', 'call'])"
                                " to see only cross-app calls, or use it alone"
                                " for every cross-app edge regardless of kind."
                            ),
                        },
                        **_ROUTING_SCHEMA_PROPERTIES,
                    },
                    "required": ["question"],
                },
            ),
            types.Tool(
                name="bcatlas_get_node",
                description="Get full details for a specific node by label or ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Node label or ID to look up"},
                        **_ROUTING_SCHEMA_PROPERTIES,
                    },
                    "required": ["label"],
                },
            ),
            types.Tool(
                name="bcatlas_find_by_global_id",
                description=(
                    "Cross-graph federation lookup: find the node(s) on THIS"
                    " graph matching a `global_id` value copied from another"
                    " graph's bcatlas_get_node output. `global_id` is a"
                    " deterministic join key stamped on every AL node (real"
                    " objects and external stubs alike), so a stub for object"
                    " X in one corpus and the real X node in its own corpus"
                    " share the same value -- use this to bridge two"
                    " independently-hosted graphs at query time. Falls back to"
                    " a type+name match when no exact match is found (the"
                    " qualifier can legitimately differ between graphs), so"
                    " an unqualified or mismatched `global_id` still returns"
                    " candidates instead of a hard miss."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "global_id": {"type": "string", "description": "global_id value to look up, e.g. from another graph's bcatlas_get_node output"},
                        **_ROUTING_SCHEMA_PROPERTIES,
                    },
                    "required": ["global_id"],
                },
            ),
            types.Tool(
                name="bcatlas_get_neighbors",
                description="Get all direct neighbors of a node with edge details.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "relation_filter": {"type": "string", "description": "Optional: filter by relation type"},
                        **_ROUTING_SCHEMA_PROPERTIES,
                    },
                    "required": ["label"],
                },
            ),
            types.Tool(
                name="bcatlas_get_signature",
                description=(
                    "Lightweight ground-truth check: the exact declaration"
                    " header (object header, or procedure/trigger signature"
                    " with its return type) for a node, re-read from the real"
                    " source file -- no body. Use this to confirm a"
                    " search/graph hit is the right one before pulling the"
                    " full body."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Node label or ID to look up"},
                        **_ROUTING_SCHEMA_PROPERTIES,
                    },
                    "required": ["label"],
                },
            ),
            types.Tool(
                name="bcatlas_get_procedure_body",
                description=(
                    "Exact, full source text of one procedure/trigger, re-read"
                    " from the real source file (not the index) -- signature,"
                    " var declarations, and every line of the body. Errors if"
                    " the node isn't inside a procedure/trigger; use"
                    " bcatlas_get_object_source for object-level nodes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Node label or ID to look up"},
                        **_ROUTING_SCHEMA_PROPERTIES,
                    },
                    "required": ["label"],
                },
            ),
            types.Tool(
                name="bcatlas_get_object_source",
                description=(
                    "Exact, full source text of the object (table/page/"
                    "codeunit/...) that a node belongs to, re-read from the"
                    " real source file. Pass either the object's own node or"
                    " any procedure inside it -- both resolve to the same"
                    " object source."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Node label or ID to look up"},
                        **_ROUTING_SCHEMA_PROPERTIES,
                    },
                    "required": ["label"],
                },
            ),
            types.Tool(
                name="bcatlas_get_community",
                description="Get all nodes in a community by community ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "community_id": {"type": "integer", "description": "Community ID (0-indexed by size)"},
                        **_ROUTING_SCHEMA_PROPERTIES,
                    },
                    "required": ["community_id"],
                },
            ),
            types.Tool(
                name="bcatlas_god_nodes",
                description="Return the most connected nodes - the core abstractions of the knowledge graph.",
                inputSchema={
                    "type": "object",
                    "properties": {"top_n": {"type": "integer", "default": 10}, **_ROUTING_SCHEMA_PROPERTIES},
                },
            ),
            types.Tool(
                name="bcatlas_graph_stats",
                description="Return summary statistics: node count, edge count, communities, confidence breakdown.",
                inputSchema={"type": "object", "properties": {**_ROUTING_SCHEMA_PROPERTIES}},
            ),
            types.Tool(
                name="bcatlas_shortest_path",
                description="Find the shortest path between two concepts in the knowledge graph.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Source concept label or keyword"},
                        "target": {"type": "string", "description": "Target concept label or keyword"},
                        "max_hops": {"type": "integer", "default": 8, "description": "Maximum hops to consider"},
                        **_ROUTING_SCHEMA_PROPERTIES,
                    },
                    "required": ["source", "target"],
                },
            ),
            types.Tool(
                name="list_prs",
                description=(
                    "List open GitHub PRs with CI status, review state, and graph impact "
                    "(which communities each PR touches, blast radius). Use this before starting "
                    "work to check if a PR already covers the area you're about to change."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "base": {"type": "string", "description": "Base branch to filter PRs by (auto-detected if omitted)"},
                        "repo": {"type": "string", "description": "GitHub repo (owner/repo). Defaults to current repo."},
                    },
                },
            ),
            types.Tool(
                name="get_pr_impact",
                description=(
                    "Get detailed graph impact for a specific PR: which files it changes, "
                    "which knowledge-graph communities are affected, and how many nodes are touched. "
                    "Use this to assess merge risk or check for overlap with your current work."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pr_number": {"type": "integer", "description": "PR number to analyse"},
                        "repo": {"type": "string", "description": "GitHub repo (owner/repo). Defaults to current repo."},
                    },
                    "required": ["pr_number"],
                },
            ),
            types.Tool(
                name="triage_prs",
                description=(
                    "Return all actionable open PRs (correct base, not stale) with full graph impact data "
                    "so you can reason about review priority, merge order, and conflict risk. "
                    "Call this when the user asks 'what PRs should I review?' or 'what's ready to merge?'"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "base": {"type": "string", "description": "Base branch to filter PRs by (auto-detected if omitted)"},
                        "repo": {"type": "string", "description": "GitHub repo (owner/repo). Defaults to current repo."},
                    },
                },
            ),
        ]

    def _tool_query_graph(arguments: dict) -> str:
        import time as _time
        from graphify import querylog
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        question = arguments["question"]
        mode = arguments.get("mode", "bfs")
        depth = min(int(arguments.get("depth", 3)), 6)
        budget = int(arguments.get("token_budget", 6000))
        context_filter = arguments.get("context_filter")
        _t0 = _time.perf_counter()
        result = _query_graph_text(
            ctx.G,
            question,
            mode=mode,
            depth=depth,
            token_budget=budget,
            context_filters=context_filter,
        )
        querylog.log_query(
            kind="mcp_query",
            question=question,
            corpus=str(ctx.graph_path),
            result=result,
            mode=mode,
            depth=depth,
            token_budget=budget,
            duration_ms=(_time.perf_counter() - _t0) * 1000,
        )
        return result

    def _ambiguity_message(G: nx.Graph, matches: _NodeMatches) -> str:
        # FR-005 (spec 004-al-object-labels): a query matching more than one
        # node in the EXACT tier is genuinely ambiguous (e.g. a table and a
        # page both named "Item") -- list the candidates by their full
        # (now type+ID-qualified) label so the caller can retry
        # unambiguously, rather than silently taking matches[0].
        candidates = "\n".join(
            f"  - {sanitize_label(G.nodes[nid].get('label', nid))}"
            for nid in matches[: matches.top_tier_count]
        )
        return (
            f"Multiple objects match:\n{candidates}\n"
            "Retry with the full object reference (e.g. the exact label "
            "shown above) to disambiguate."
        )

    def _tool_get_node(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        label = arguments["label"].lower()
        matches = _find_node(ctx.G, label)
        if not matches:
            return f"No node matching '{label}' found."
        if matches.top_tier_count > 1:
            return _ambiguity_message(ctx.G, matches)
        nid = matches[0]
        d = ctx.G.nodes[nid]
        # Sanitise every LLM-derived field before concatenation (F-010).
        lines = [
            f"Node: {sanitize_label(d.get('label', nid))}",
            f"  ID: {sanitize_label(nid)}",
            f"  Source: {sanitize_label(str(d.get('source_file', '')))} {sanitize_label(str(d.get('source_location', '')))}",
            f"  Type: {sanitize_label(str(d.get('file_type', '')))}",
            f"  Community: {sanitize_label(str(d.get('community_name') or d.get('community', '')))}",
            f"  Degree: {ctx.G.degree(nid)}",
        ]
        # global_id/al_owning_app only exist on AL nodes (bc-code-atlas #26/#27)
        # -- omitted rather than printed blank for every non-AL node/corpus.
        global_id = d.get("global_id")
        if global_id:
            lines.append(f"  GlobalID: {sanitize_label(str(global_id))}")
        owning_app = d.get("al_owning_app")
        if owning_app:
            lines.append(f"  OwningApp: {sanitize_label(str(owning_app))}")
        return "\n".join(lines)

    def _tool_find_by_global_id(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        global_id = arguments["global_id"]
        node_ids = _find_node_by_global_id(ctx.G, global_id)
        if not node_ids:
            return f"No node with global_id '{sanitize_label(global_id)}' found on this graph."
        # #31: an empty result would have gone through the type+name fallback
        # too, so any non-empty result here is exact only when its own
        # global_id equals the query verbatim -- otherwise every node in it
        # matched by type+name alone (qualifier differed), and the caller
        # should know that's a lower-confidence resolution.
        exact = ctx.G.nodes[node_ids[0]].get("global_id") == global_id
        if exact:
            lines = [f"{len(node_ids)} node(s) matching global_id '{sanitize_label(global_id)}':"]
        else:
            lines = [
                f"global_id '{sanitize_label(global_id)}' not found exactly.",
                f"{len(node_ids)} candidate(s) by type+name:",
            ]
        for nid in node_ids:
            d = ctx.G.nodes[nid]
            lines.append(
                f"  {sanitize_label(d.get('label', nid))}"
                f" (ID: {sanitize_label(nid)},"
                f" Source: {sanitize_label(str(d.get('source_file', '')))}"
                f" {sanitize_label(str(d.get('source_location', '')))})"
            )
        return "\n".join(lines)

    def _tool_get_neighbors(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        G = ctx.G
        label = arguments["label"].lower()
        rel_filter = arguments.get("relation_filter", "").lower()
        matches = _find_node(G, label)
        if not matches:
            return f"No node matching '{label}' found."
        if matches.top_tier_count > 1:
            return _ambiguity_message(G, matches)
        nid = matches[0]
        lines = [f"Neighbors of {sanitize_label(G.nodes[nid].get('label', nid))}:"]
        for nb in G.successors(nid):
            d = edge_data(G, nid, nb)
            rel = d.get("relation", "")
            if rel_filter and rel_filter not in rel.lower():
                continue
            lines.append(
                f"  --> {sanitize_label(G.nodes[nb].get('label', nb))} "
                f"[{sanitize_label(str(rel))}] [{sanitize_label(str(d.get('confidence', '')))}]"
            )
        for nb in G.predecessors(nid):
            d = edge_data(G, nb, nid)
            rel = d.get("relation", "")
            if rel_filter and rel_filter not in rel.lower():
                continue
            lines.append(
                f"  <-- {sanitize_label(G.nodes[nb].get('label', nb))} "
                f"[{sanitize_label(str(rel))}] [{sanitize_label(str(d.get('confidence', '')))}]"
            )
        return "\n".join(lines)

    def _tool_source_lookup(ctx: _RoutedGraph, label: str, fn) -> str:
        matches = _find_node(ctx.G, label.lower())
        if not matches:
            return f"No node matching '{label}' found."
        if matches.top_tier_count > 1:
            return _ambiguity_message(ctx.G, matches)
        nid = matches[0]
        d = ctx.G.nodes[nid]
        source_file = d.get("source_file") or ""
        if not source_file:
            return f"Node '{sanitize_label(d.get('label', nid))}' has no associated source file."
        try:
            source_path = validate_graph_path(ctx.source_root / source_file, base=ctx.source_root)
        except FileNotFoundError:
            return f"Source file not found on disk: {source_file}"
        except ValueError:
            return f"Source path escapes the indexed source root: {source_file}"
        try:
            return fn(source_path, d.get("source_location"))
        except source_lookup.SourceLookupError as exc:
            return str(exc)

    def _tool_get_signature(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        return _tool_source_lookup(ctx, arguments["label"], source_lookup.get_signature)

    def _tool_get_procedure_body(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        return _tool_source_lookup(ctx, arguments["label"], source_lookup.get_procedure_body)

    def _tool_get_object_source(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        return _tool_source_lookup(ctx, arguments["label"], source_lookup.get_object_source)

    def _tool_get_community(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        cid = int(arguments["community_id"])
        nodes = ctx.communities.get(cid, [])
        if not nodes:
            return f"Community {cid} not found."
        lines = [f"Community {cid} ({len(nodes)} nodes):"]
        for n in nodes:
            d = ctx.G.nodes[n]
            # Sanitise label and source_file (F-010).
            lines.append(
                f"  {sanitize_label(d.get('label', n))} "
                f"[{sanitize_label(str(d.get('source_file', '')))}]"
            )
        return "\n".join(lines)

    def _tool_god_nodes(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        from graphify.analyze import god_nodes as _god_nodes
        nodes = _god_nodes(ctx.G, top_n=int(arguments.get("top_n", 10)))
        lines = ["God nodes (most connected):"]
        lines += [f"  {i}. {n['label']} - {n['degree']} edges" for i, n in enumerate(nodes, 1)]
        return "\n".join(lines)

    def _tool_graph_stats(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        G = ctx.G
        confs = [d.get("confidence", "EXTRACTED") for _, _, d in G.edges(data=True)]
        total = len(confs) or 1
        return (
            f"Nodes: {G.number_of_nodes()}\n"
            f"Edges: {G.number_of_edges()}\n"
            f"Communities: {len(ctx.communities)}\n"
            f"EXTRACTED: {round(confs.count('EXTRACTED')/total*100)}%\n"
            f"INFERRED: {round(confs.count('INFERRED')/total*100)}%\n"
            f"AMBIGUOUS: {round(confs.count('AMBIGUOUS')/total*100)}%\n"
        )

    def _tool_shortest_path(arguments: dict) -> str:
        try:
            ctx = _resolve_ctx(arguments)
        except _RoutingError as exc:
            return str(exc)
        G = ctx.G
        src_scored = _score_nodes(G, [t.lower() for t in arguments["source"].split()])
        tgt_scored = _score_nodes(G, [t.lower() for t in arguments["target"].split()])
        if not src_scored:
            return f"No node matching source '{arguments['source']}' found."
        if not tgt_scored:
            return f"No node matching target '{arguments['target']}' found."
        src_nid, tgt_nid = src_scored[0][1], tgt_scored[0][1]
        # Ambiguity guard: when both queries resolve to the same node, the
        # shortest path is trivially zero hops, which is almost never what the
        # caller wanted (see bug #828).
        if src_nid == tgt_nid:
            return (
                f"'{arguments['source']}' and '{arguments['target']}' both resolved to "
                f"the same node '{src_nid}'. Use a more specific label or the exact node ID."
            )
        warnings: list[str] = []
        for name, scored in (("source", src_scored), ("target", tgt_scored)):
            if len(scored) >= 2:
                top, runner = scored[0][0], scored[1][0]
                if top > 0 and (top - runner) / top < 0.10:
                    warnings.append(
                        f"warning: {name} match was ambiguous "
                        f"(top score {top:g}, runner-up {runner:g})"
                    )
        max_hops = int(arguments.get("max_hops", 8))
        try:
            # Use undirected view for path-finding (works regardless of query src/tgt order)
            path_nodes = nx.shortest_path(G.to_undirected(as_view=True), src_nid, tgt_nid)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return f"No path found between '{G.nodes[src_nid].get('label', src_nid)}' and '{G.nodes[tgt_nid].get('label', tgt_nid)}'."
        hops = len(path_nodes) - 1
        if hops > max_hops:
            return f"Path exceeds max_hops={max_hops} ({hops} hops found)."
        segments = []
        for i in range(len(path_nodes) - 1):
            u, v = path_nodes[i], path_nodes[i + 1]
            if G.has_edge(u, v):
                edata = edge_data(G, u, v)
                forward = True
            else:
                edata = edge_data(G, v, u)
                forward = False
            rel = edata.get("relation", "")
            conf = edata.get("confidence", "")
            conf_str = f" [{conf}]" if conf else ""
            if i == 0:
                segments.append(G.nodes[u].get("label", u))
            if forward:
                segments.append(f"--{rel}{conf_str}--> {G.nodes[v].get('label', v)}")
            else:
                segments.append(f"<--{rel}{conf_str}-- {G.nodes[v].get('label', v)}")
        prefix = ("\n".join(warnings) + "\n") if warnings else ""
        return prefix + f"Shortest path ({hops} hops):\n  " + " ".join(segments)

    def _tool_list_prs(arguments: dict) -> str:
        from graphify.prs import fetch_prs, fetch_worktrees, format_prs_text, _detect_default_branch
        repo = arguments.get("repo") or None
        base = arguments.get("base") or _detect_default_branch(repo)
        try:
            prs = fetch_prs(repo=repo, base=base)
        except RuntimeError as e:
            return f"Error: {e}"
        worktrees = fetch_worktrees()
        for pr in prs:
            pr.worktree_path = worktrees.get(pr.branch)
        return format_prs_text(prs, base)

    def _tool_get_pr_impact(arguments: dict) -> str:
        from graphify.prs import fetch_pr_files, compute_pr_impact, _gh, _parse_ci
        number = int(arguments["pr_number"])
        repo = arguments.get("repo") or None
        # Use gh pr view directly — works for any base branch, not just the default
        view_args = ["pr", "view", str(number), "--json",
                     "title,headRefName,baseRefName,author,isDraft,reviewDecision,statusCheckRollup,updatedAt"]
        if repo:
            view_args += ["--repo", repo]
        pr_data = _gh(*view_args)
        if pr_data is None:
            return f"PR #{number} not found or gh not authenticated."
        files = fetch_pr_files(number, repo)
        if not files:
            return f"PR #{number}: no changed files found (may require gh auth)."
        comms, nodes = compute_pr_impact(files, G)
        ci = _parse_ci(pr_data.get("statusCheckRollup") or [])
        lines = [
            f"PR #{number}: {pr_data['title']}",
            f"CI: {ci}  Review: {pr_data.get('reviewDecision') or 'none'}",
            f"Base: {pr_data['baseRefName']}  Author: {(pr_data.get('author') or {}).get('login', '?')}",
            f"\nGraph impact: {nodes} nodes across {len(comms)} communities",
            f"Communities touched: {comms}",
            f"Files changed ({len(files)}):",
        ]
        lines += [f"  {f}" for f in files[:20]]
        if len(files) > 20:
            lines.append(f"  … and {len(files) - 20} more")
        return "\n".join(lines)

    def _tool_triage_prs(arguments: dict) -> str:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from graphify.prs import fetch_prs, fetch_worktrees, fetch_pr_files, compute_pr_impact, _STATUS_ORDER, _detect_default_branch
        repo = arguments.get("repo") or None
        base = arguments.get("base") or _detect_default_branch(repo)
        try:
            prs = fetch_prs(repo=repo, base=base)
        except RuntimeError as e:
            return f"Error: {e}"
        worktrees = fetch_worktrees()
        for pr in prs:
            pr.worktree_path = worktrees.get(pr.branch)
        actionable = [p for p in prs if p.base_branch == base and p.status not in ("WRONG-BASE", "STALE")]
        if not actionable:
            return f"No actionable PRs targeting {base}."
        # Fetch diffs concurrently then compute graph impact using in-memory G
        workers = min(8, len(actionable))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_to_pr = {pool.submit(fetch_pr_files, pr.number, repo): pr for pr in actionable}
            for fut in as_completed(future_to_pr):
                pr = future_to_pr[fut]
                try:
                    files = fut.result()
                except Exception:
                    files = []
                if files:
                    pr.files_changed = files
                    pr.communities_touched, pr.nodes_affected = compute_pr_impact(files, G)
        header = (
            f"Actionable PRs targeting {base}: {len(actionable)}\n"
            "Rank these by review priority. Higher blast_radius = more graph communities affected = higher merge risk.\n"
        )
        lines = [header]
        for p in sorted(actionable, key=lambda x: (_STATUS_ORDER.index(x.status) if x.status in _STATUS_ORDER else 99)):
            impact = f"  blast_radius={p.blast_radius}" if p.blast_radius else ""
            wt = f"  worktree={p.worktree_path}" if p.worktree_path else ""
            lines.append(
                f"PR #{p.number} [{p.status}] CI={p.ci_status} review={p.review_decision or 'none'} "
                f"age={p.days_old}d author={p.author}{impact}{wt}\n  title: {p.title}"
            )
        return "\n\n".join(lines)

    _handlers = {
        "bcatlas_query_graph": _tool_query_graph,
        "bcatlas_get_node": _tool_get_node,
        "bcatlas_find_by_global_id": _tool_find_by_global_id,
        "bcatlas_get_neighbors": _tool_get_neighbors,
        "bcatlas_get_signature": _tool_get_signature,
        "bcatlas_get_procedure_body": _tool_get_procedure_body,
        "bcatlas_get_object_source": _tool_get_object_source,
        "bcatlas_get_community": _tool_get_community,
        "bcatlas_god_nodes": _tool_god_nodes,
        "bcatlas_graph_stats": _tool_graph_stats,
        "bcatlas_shortest_path": _tool_shortest_path,
        "list_prs": _tool_list_prs,
        "get_pr_impact": _tool_get_pr_impact,
        "triage_prs": _tool_triage_prs,
    }

    def _load_community_labels() -> dict[int, str]:
        labels_path = Path(graph_path).parent / ".graphify_labels.json"
        if labels_path.exists():
            try:
                return {int(k): v for k, v in json.loads(labels_path.read_text(encoding="utf-8")).items()}
            except Exception:
                pass
        return {cid: f"Community {cid}" for cid in communities}

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(uri=AnyUrl("graphify://report"), name="Graph Report", description="Full GRAPH_REPORT.md", mimeType="text/markdown"),
            types.Resource(uri=AnyUrl("graphify://stats"), name="Graph Stats", description="Node/edge/community counts and confidence breakdown", mimeType="text/plain"),
            types.Resource(uri=AnyUrl("graphify://god-nodes"), name="God Nodes", description="Top 10 most-connected nodes", mimeType="text/plain"),
            types.Resource(uri=AnyUrl("graphify://surprises"), name="Surprising Connections", description="Cross-community surprising connections", mimeType="text/plain"),
            types.Resource(uri=AnyUrl("graphify://audit"), name="Confidence Audit", description="EXTRACTED/INFERRED/AMBIGUOUS edge breakdown", mimeType="text/plain"),
            types.Resource(uri=AnyUrl("graphify://questions"), name="Suggested Questions", description="Suggested questions for this codebase", mimeType="text/plain"),
        ]

    @server.read_resource()
    async def read_resource(uri: AnyUrl) -> str:
        _maybe_reload()
        uri_str = str(uri)
        if uri_str == "graphify://report":
            report_path = Path(graph_path).parent / "GRAPH_REPORT.md"
            if report_path.exists():
                return report_path.read_text(encoding="utf-8")
            return "GRAPH_REPORT.md not found. Run graphify extract first."
        if uri_str == "graphify://stats":
            return _tool_graph_stats({})
        if uri_str == "graphify://god-nodes":
            return _tool_god_nodes({"top_n": 10})
        if uri_str == "graphify://surprises":
            try:
                from graphify.analyze import surprising_connections
                surprises = surprising_connections(G, communities, top_n=10)
                if not surprises:
                    return "No surprising connections found."
                lines = ["Surprising cross-community connections:"]
                for s in surprises:
                    lines.append(f"  {s.get('source', '')} <-> {s.get('target', '')} [{s.get('relation', '')}]")
                return "\n".join(lines)
            except Exception as exc:
                return f"Could not compute surprising connections: {exc}"
        if uri_str == "graphify://audit":
            confs = [d.get("confidence", "EXTRACTED") for _, _, d in G.edges(data=True)]
            total = len(confs) or 1
            return (
                f"Total edges: {total}\n"
                f"EXTRACTED: {confs.count('EXTRACTED')} ({round(confs.count('EXTRACTED')/total*100)}%)\n"
                f"INFERRED: {confs.count('INFERRED')} ({round(confs.count('INFERRED')/total*100)}%)\n"
                f"AMBIGUOUS: {confs.count('AMBIGUOUS')} ({round(confs.count('AMBIGUOUS')/total*100)}%)\n"
            )
        if uri_str == "graphify://questions":
            try:
                from graphify.analyze import suggest_questions
                community_labels = _load_community_labels()
                questions = suggest_questions(G, communities, community_labels, top_n=10)
                if not questions:
                    return "No suggested questions available."
                lines = ["Suggested questions:"]
                for q in questions:
                    if isinstance(q, dict):
                        lines.append(f"  - {q.get('question', '')}")
                    else:
                        lines.append(f"  - {q}")
                return "\n".join(lines)
            except Exception as exc:
                return f"Could not generate questions: {exc}"
        raise ValueError(f"Unknown resource: {uri_str}")

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        _maybe_reload()
        handler = _handlers.get(name)
        if not handler:
            return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            return [types.TextContent(type="text", text=handler(arguments))]
        except Exception as exc:
            return [types.TextContent(type="text", text=f"Error executing {name}: {exc}")]

    return server


def serve(
    graph_path: str | None = None,
    *,
    instructions: str | None = None,
    source_root: str | None = None,
    data_root: str | None = None,
) -> None:
    """Start the MCP server over stdio (the default, per-developer transport)."""
    graph_path = graph_path or _default_graph_json()
    try:
        from mcp.server.stdio import stdio_server
    except ImportError as e:
        raise ImportError('mcp not installed. Run: pip install "graphifyy[mcp]"') from e
    import asyncio

    server = _build_server(
        graph_path, instructions=instructions, source_root=source_root, data_root=data_root
    )

    async def main() -> None:
        async with stdio_server() as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    _filter_blank_stdin()
    asyncio.run(main())


class _MCPASGIApp:
    """Raw-ASGI wrapper around the Streamable HTTP session manager.

    Passed to a Starlette ``Route`` as a class instance (not a function) so
    Starlette treats it as an ASGI app: it serves the exact mount path for all
    methods (GET/POST/DELETE) with no request/response wrapping and no
    trailing-slash redirect — mirroring how FastMCP mounts the same manager.
    """

    def __init__(self, manager) -> None:
        self._manager = manager

    async def __call__(self, scope, receive, send) -> None:
        await self._manager.handle_request(scope, receive, send)


class _ApiKeyMiddleware:
    """Pure-ASGI API-key gate for the HTTP transport.

    Implemented as raw ASGI (not Starlette's BaseHTTPMiddleware) on purpose:
    BaseHTTPMiddleware buffers responses and breaks the Streamable HTTP SSE
    stream. This short-circuits with 401 before the request ever reaches the
    session manager, leaving the streaming path untouched for authorized calls.
    """

    def __init__(self, app, api_key: str) -> None:
        self.app = app
        self._expected = api_key.encode("utf-8")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        import hmac
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"x-api-key")
        if provided is None:
            # RFC 6750: the auth scheme token is case-insensitive.
            scheme, _, token = headers.get(b"authorization", b"").partition(b" ")
            if scheme.lower() == b"bearer" and token:
                provided = token.strip()
        # Constant-time compare; reject when no key was supplied at all.
        if provided is None or not hmac.compare_digest(provided, self._expected):
            body = b'{"error": "unauthorized"}'
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def _build_http_app(
    graph_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    api_key: str | None = None,
    path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
    session_timeout: float | None = 3600.0,
    instructions: str | None = None,
    source_root: str | None = None,
    data_root: str | None = None,
):
    """Build the Starlette ASGI app for the Streamable HTTP transport.

    Split out from :func:`serve_http` (which blocks on uvicorn) so the wiring
    can be exercised with an in-process ASGI test client.

    ``session_timeout`` reaps stateful sessions idle for that many seconds so a
    long-running shared server does not leak memory when IDE clients disconnect
    without sending a DELETE. ``None`` (or <= 0) disables reaping; it is forced
    to ``None`` in stateless mode, which has no sessions to reap.
    """
    try:
        import contextlib

        from starlette.applications import Starlette
        from starlette.middleware import Middleware
        from starlette.routing import Route

        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings
    except ImportError as e:
        raise ImportError(
            'HTTP transport needs the mcp extra (mcp + starlette + uvicorn). '
            'Run: pip install "graphifyy[mcp]"'
        ) from e

    # A blank key (e.g. --api-key "" or an empty GRAPHIFY_API_KEY) must not be
    # mistaken for "auth on" — normalize it to None so the gate is unambiguous.
    api_key = (api_key or "").strip() or None

    server = _build_server(
        graph_path, instructions=instructions, source_root=source_root, data_root=data_root
    )

    # DNS-rebinding protection. When the operator binds a wildcard address they
    # are intentionally exposing the server, so accept any Host header; for a
    # loopback/specific bind, restrict Host to that address (with and without
    # the port) plus the localhost aliases.
    if host in ("0.0.0.0", "::", ""):
        security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    else:
        allowed = {host, "localhost", "127.0.0.1"}
        allowed |= {f"{h}:{port}" for h in list(allowed)}
        security = TransportSecuritySettings(allowed_hosts=sorted(allowed))

    # The SDK rejects a non-positive timeout and forbids one in stateless mode.
    idle_timeout = None if (stateless or not session_timeout or session_timeout <= 0) else session_timeout

    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=json_response,
        stateless=stateless,
        security_settings=security,
        session_idle_timeout=idle_timeout,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        # The session manager owns an anyio task group that must wrap the whole
        # server lifetime, so enter it here rather than per-request.
        async with manager.run():
            yield

    middleware = []
    if api_key:
        middleware.append(Middleware(_ApiKeyMiddleware, api_key=api_key))

    return Starlette(
        routes=[Route(path, endpoint=_MCPASGIApp(manager))],
        middleware=middleware,
        lifespan=lifespan,
    )


def serve_http(
    graph_path: str | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    api_key: str | None = None,
    path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
    session_timeout: float | None = 3600.0,
    instructions: str | None = None,
    source_root: str | None = None,
    data_root: str | None = None,
) -> None:
    """Start the MCP server over Streamable HTTP (MCP spec 2025-03-26).

    Serves the same tools/resources as the stdio transport, so a single shared
    process can host the graph for a whole team. Clients point their IDE MCP
    config at ``http://<host>:<port><path>`` (default ``/mcp``).

    ``api_key`` (or the ``GRAPHIFY_API_KEY`` env var) enables a simple header
    check (``Authorization: Bearer <key>`` or ``X-API-Key: <key>``). OAuth is a
    deliberate follow-up. Binding ``0.0.0.0`` exposes the server beyond
    localhost — set an api_key when you do.
    """
    graph_path = graph_path or _default_graph_json()
    try:
        import uvicorn
    except ImportError as e:
        raise ImportError(
            'HTTP transport needs the mcp extra (mcp + starlette + uvicorn). '
            'Run: pip install "graphifyy[mcp]"'
        ) from e

    api_key = (api_key or "").strip() or None

    app = _build_http_app(
        graph_path,
        host=host,
        port=port,
        api_key=api_key,
        path=path,
        json_response=json_response,
        stateless=stateless,
        session_timeout=session_timeout,
        instructions=instructions,
        source_root=source_root,
        data_root=data_root,
    )

    auth_note = "api-key required" if api_key else "no auth (set --api-key to require one)"
    print(
        f"graphify MCP server (streamable-http) on http://{host}:{port}{path} - {auth_note}",
        file=sys.stderr,
    )
    if host in ("0.0.0.0", "::", "") and not api_key:
        print(
            f"WARNING: binding {host or '0.0.0.0'} with no api-key exposes the graph "
            "unauthenticated on the network. Set --api-key (or GRAPHIFY_API_KEY).",
            file=sys.stderr,
        )
    uvicorn.run(app, host=host, port=port)


def _main(argv: list[str] | None = None) -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser(
        prog="python -m graphify.serve",
        description="Serve a graphify knowledge graph over MCP (stdio or Streamable HTTP).",
    )
    parser.add_argument(
        "graph_path",
        nargs="?",
        default=None,
        help="Path to graph.json (default: graphify-out/graph.json)",
    )
    parser.add_argument(
        "--graph",
        dest="graph_flag",
        default=None,
        metavar="PATH",
        help="Path to graph.json — alias for the positional argument",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to serve on (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP bind port (default: 8080)")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("GRAPHIFY_API_KEY"),
        help="Require this key on the HTTP transport (env: GRAPHIFY_API_KEY)",
    )
    parser.add_argument("--path", default="/mcp", help="HTTP mount path (default: /mcp)")
    parser.add_argument(
        "--json-response",
        action="store_true",
        help="Return plain JSON responses instead of SSE streams",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        help="Run without per-session state (for load-balanced / CI deployments)",
    )
    parser.add_argument(
        "--session-timeout",
        type=float,
        default=3600.0,
        help="Reap stateful sessions idle this many seconds (default: 3600; 0 disables)",
    )
    parser.add_argument(
        "--instructions",
        default=os.environ.get("GRAPHIFY_INSTRUCTIONS"),
        help=(
            "MCP server instructions describing what graph/corpus this is "
            "(env: GRAPHIFY_INSTRUCTIONS). graphify is corpus-agnostic, so "
            "this is unset by default -- set it when serving a specific "
            "codebase so an agent with no prior context knows what it's "
            "looking at."
        ),
    )
    parser.add_argument(
        "--source-root",
        default=os.environ.get("GRAPHIFY_SOURCE_ROOT"),
        help=(
            "Root directory that source_file paths in graph.json are relative"
            " to (env: GRAPHIFY_SOURCE_ROOT). Used by bcatlas_get_signature/"
            " bcatlas_get_procedure_body/bcatlas_get_object_source to re-read exact source."
            " Defaults to graph_path's grandparent directory, which matches"
            " every existing deployment layout."
        ),
    )
    parser.add_argument(
        "--data-root",
        default=os.environ.get("GRAPHIFY_BCATLAS_DATA_ROOT"),
        help=(
            "Root under which multi-tenant routed country/version graphs"
            " live, as `<data-root>/warm/<country>/<version>/graph/graph.json`"
            " (env: GRAPHIFY_BCATLAS_DATA_ROOT). Only consulted when a tool"
            " call supplies both `country` and `version` -- every tool's"
            " zero-argument behavior is unaffected. Defaults to `<repo"
            " root>/data`, matching bc-code-atlas's own layout convention"
            " (build/build/layout.py)."
        ),
    )
    args = parser.parse_args(argv)
    graph_path = args.graph_flag or args.graph_path or _default_graph_json()

    if args.transport == "http":
        serve_http(
            graph_path,
            host=args.host,
            port=args.port,
            api_key=args.api_key,
            path=args.path,
            json_response=args.json_response,
            stateless=args.stateless,
            session_timeout=args.session_timeout,
            instructions=args.instructions,
            source_root=args.source_root,
            data_root=args.data_root,
        )
    else:
        serve(
            graph_path,
            instructions=args.instructions,
            source_root=args.source_root,
            data_root=args.data_root,
        )


if __name__ == "__main__":
    _main()
