# AL / Business Central support

This fork teaches [graphify](https://github.com/safishamsi/graphify) to understand **AL**, the
language of Microsoft Dynamics 365 Business Central. Upstream graphify ignores `.al` files; this
fork parses them with the [`tree-sitter-al`](https://github.com/SShadowS/tree-sitter-al) grammar
and adds a BC-specific semantic layer on top of graphify's generic extractor.

## Install

```bash
git clone <this-repo> graphify-al && cd graphify-al
uv venv && uv pip install -e . tree-sitter-al
# or: pip install -e . tree-sitter-al
```

`tree-sitter-al` is a normal PyPI dependency; it is not vendored here.

## Build a graph for an AL repo

Extraction is fully local and needs no API key:

```bash
python -m graphify update /path/to/your/al/app
```

This writes `graphify-out/{graph.json,graph.html,GRAPH_REPORT.md}` next to the code. Re-running
re-extracts only changed files (SHA cache).

## Query it

```bash
python -m graphify query   "SomeCodeunit"            --graph /path/to/your/al/app/graphify-out/graph.json
python -m graphify affected "Some Setup Table"       --graph .../graph.json   # reverse: what depends on it
python -m graphify path    "CodeunitA" "CodeunitB"   --graph .../graph.json   # shortest path
python -m graphify explain "SomeCodeunit"            --graph .../graph.json
```

Node labels: AL objects are their name (`MyCodeunit`, `"My Quoted Object"`); procedures/triggers
are `.ProcName()`. `examples/` has a tiny synthetic app you can build and query end-to-end.

## What the AL layer extracts

| Relation | Meaning |
|---|---|
| `contains` / `method` | file → object (codeunit, table, page, report, query, xmlport, enum, interface, extensions) → its procedures/triggers |
| `calls` | intra-object procedure calls, type-resolved cross-object Codeunit calls (`MyCdu.DoThing()` resolved via the variable's declared type), indirect dispatch `Codeunit.Run` / `Page.Run(Modal)` / `Report.Run(...)` resolved via the `Object::"Name"` argument, **plus** interface dispatch (`IFoo.Method()` on an `Interface "IFoo"`-typed variable) fanned out to `Method` on every object that `implements "IFoo"` |
| `subscribes` | `[EventSubscriber]` → the publisher object named in the attribute (objects outside the analyzed corpus become tagged `external` nodes — the integration surface) |
| `extends` | `tableextension` / `pageextension` / etc. → its base object |
| `binds` | page `SourceTable`, report/query `dataitem`, xmlport `tableelement` → the table it binds |
| `relates_to` | a field's `TableRelation` → the foreign-key target table(s) |
| `computes_from` | a table's FlowField `CalcFormula` (`Sum`/`Count`/`Exist`/`Lookup`/`Average`/`Min`/`Max`) → the source table it aggregates (source field kept on the edge; tables outside the corpus become tagged `external` nodes) |
| `implements` | object → each interface in its `implements` clause; an enum value's `Implementation = IFace = Impl` also links the concrete impl (`enum_binds_implementation`) and that impl → the interface |
| `imports` | `using` namespace directives |

## Honest limitations

- **Cross-object call resolution is partial.** Statically-typed calls, the
  `Codeunit.Run` / `Page.RunModal` / `Report.Run` dispatch idioms, and interface
  dispatch all resolve. Interface dispatch is a *fan-out*: a call on an
  `Interface "IFoo"`-typed variable produces a `calls` edge to that method on every
  object implementing `IFoo` (the static analysis can't know which concrete
  implementation runs, so it links all candidates — an over- rather than
  under-approximation). Service-locator / reflection patterns still can't be
  followed statically; use the `subscribes` edges to see event wiring.
- **The static `GRAPH_REPORT.md` god-nodes / "surprising connections" sections are object-only.**
  graphify's report treats `.foo()` labels as synthetic stubs (a Python/JS assumption), which
  excludes AL procedures *from that report section only*. The `query`/`affected`/`path` engine
  traverses procedures fine — trust queries over that report section for AL.
- **For precise "definition of / all references to one symbol", an AL language server is better.**
  graphify is for the bird's-eye map and multi-hop traversal across many objects/files.

## Implementation

All AL code is isolated to two files:

- `graphify/detect.py` — `.al` added to `CODE_EXTENSIONS`.
- `graphify/extract.py` — `_AL_CONFIG` (drives the generic extractor), `extract_al`,
  `_al_collect_facts` (per-file AL facts), `_resolve_al_facts` (global cross-object resolution),
  and `_import_al`. Wired into `_DISPATCH` and `extract()`.
