# al-translations

Turns an AL app's `*.de-DE.xlf` (or any target language) into a `translations.json`
sidecar that `graphify/export.py` merges onto graph nodes as `caption_de`,
`tooltip_de`, `*_de_state` and `messages_de`.

Why: captions in AL source are English while the end user's client is localized. A
graph that only carries the English caption cannot answer "which node is behind the
button the user sees", and error message texts (AL `Label` declarations) have no node
at all. Both are the information documentation work starts from.

## Usage

    python xlf2nodes_hash.py <path-to-al-repo-root>

Expects `<root>/graphify-out/graph.json` to exist and a `*.de-DE.xlf` under
`<root>/app/Translations/`. Writes `<root>/translations.json`.

## How the mapping works

Trans-unit ids are hash chains, e.g.

    Codeunit 3710261545 - Method 2498012481 - NamedType 2025085014

Every segment - object name, member name AND the property name - is hashed with the
AL variant of FNV-1a (`alfnv.py`). Mapping by hash instead of by English source text
is exact: it survives caption changes and never has to guess between two identical
English captions in one object.

Two traps worth knowing:
- `Property <hash>` is a hash too, not the literal word. It must be excluded from the
  member chain, or an object-level caption gets the property hash as its member.
- For an object node the object *is* the node; walking up the ownership chain lands on
  the file node and yields nothing.

Measured on one app (497 trans units): 338 captions, 179 tooltips, 34 message texts
mapped, 10 misses - all fields of a temporary table that carry no `Caption` in code.

## Attribution

`alfnv.py` reimplements the AL name-hash documented in **nab-al-tools** by Johannes
Wikman (MIT, Copyright (c) 2019 Johannes Wikman), file
`extension/src/AlFunctions.ts`, function `alFnv`:
<https://github.com/jwikman/nab-al-tools>

That implementation in turn documents the algorithm as the Roslyn hash method as used
by the Business Central platform. Differences from textbook FNV-1a that make or break
it: UTF-16LE input, signed 32-bit arithmetic, and `+ Int32.MaxValue` on the result.
Test vector: `al_fnv("") == 18652612`.
