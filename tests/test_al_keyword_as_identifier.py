"""tree-sitter-al 4.0.1 adds 14 section keywords to `keyword_as_identifier`
(CHANGELOG "Fixed", v4.0.1): `filter`, `column`, `dataitem`, `elements`,
`fields`, `keys`, `labels`, `layout`, `modify`, `rendering`, `requestpage`,
`schema`, `views`, `analysisviews`. Before that fix (present since at least
2.5.1, i.e. every version graphify has ever depended on before the v4 pin),
a global variable named one of these words produced an `ERROR` node at the
declaration -- and tree-sitter's error recovery can swallow neighboring
declarations along with it, not just the misparsed line (#2551 in this repo's
own extract.py).

These are regression guards, not new extraction features: nothing in
`extract.py` needed to change for this fix (AL extraction walks the tree by
node type, never by comparing token text against a keyword list -- see the
PR discussion), so the guard is purely "does the parser still accept this",
protecting against a future downgrade or grammar regression re-breaking
extraction on real Business Central code that happens to name a variable
`Filter`, `Schema`, `Layout`, etc.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_al")

from graphify.extract import extract  # noqa: E402

# The exact 14 words tree-sitter-al 4.0.1 added to keyword_as_identifier.
_NEWLY_LEGAL_WORDS = [
    "filter", "column", "dataitem", "elements", "fields", "keys", "labels",
    "layout", "modify", "rendering", "requestpage", "schema", "views",
    "analysisviews",
]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _labels(result: dict) -> set[str]:
    return {n.get("label", "") for n in result["nodes"]}


@pytest.mark.parametrize("word", _NEWLY_LEGAL_WORDS)
def test_global_var_named_after_newly_legal_keyword_extracts_cleanly(
    tmp_path: Path, capsys, word: str,
) -> None:
    var_name = word.capitalize()
    p = _write(tmp_path / f"Sample{var_name}.Codeunit.al", (
        f'codeunit 50100 "Sample {var_name}"\n'
        "{\n"
        "    var\n"
        f"        {var_name}: Text;\n"
        "\n"
        "    procedure Foo()\n"
        "    begin\n"
        f"        {var_name} := 'x';\n"
        "    end;\n"
        "\n"
        "    procedure Bar()\n"
        "    begin\n"
        "    end;\n"
        "}\n"
    ))
    result = extract([p], cache_root=tmp_path / "cache")

    # #2551: a syntax-error warning would mean the parser hit an ERROR at (or
    # near) the declaration -- the exact failure mode this word list used to
    # trigger on every version graphify has depended on before tree-sitter-al 4.
    err = capsys.readouterr().err
    assert "syntax errors" not in err, (
        f"var named {var_name!r} (a keyword_as_identifier word since "
        f"tree-sitter-al 4.0.1) produced a parse error: {err}"
    )

    labels = _labels(result)
    # Both procedures must survive -- ERROR recovery around the var
    # declaration can silently drop what follows it, not just the
    # misparsed line.
    assert ".Foo()" in labels, f"procedure after `{var_name}` var lost: {labels}"
    assert ".Bar()" in labels, f"procedure after `{var_name}` var lost: {labels}"
