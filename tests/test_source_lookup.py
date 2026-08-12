"""Regression tests for graphify.source_lookup (issue #24 in bc-code-atlas).

get_signature/get_procedure_body were returning just the bare word
"procedure" for AL procedures whenever `_collect_spans`'s top-down,
unconditional overwrite let a same-typed descendant (tree-sitter-al's
literal "procedure" keyword token) clobber the outermost real declaration
match. get_signature was separately including the trailing var section
(local variable declarations between the signature and the body) as part
of the header. Both are fixed by keeping the first (outermost) span match
and cutting the header at the var section instead of only the body.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_al")

from graphify.source_lookup import get_procedure_body, get_signature


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_get_signature_returns_full_header_not_bare_keyword(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "Test.Codeunit.al",
        "codeunit 50100 MyCod\n"
        "{\n"
        "    procedure DoThing(X: Integer): Boolean\n"
        "    begin\n"
        "        exit(true);\n"
        "    end;\n"
        "}\n",
    )

    signature = get_signature(p, "Line 3")

    assert signature == "procedure DoThing(X: Integer): Boolean"


def test_get_signature_excludes_var_section(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "Test.Codeunit.al",
        "codeunit 50100 MyCod\n"
        "{\n"
        "    procedure DoThing()\n"
        "    var\n"
        "        MyRecord: Record MyTable;\n"
        "    begin\n"
        "        exit;\n"
        "    end;\n"
        "}\n",
    )

    signature = get_signature(p, "Line 3")

    assert signature == "procedure DoThing()"
    assert "var" not in signature
    assert "MyRecord" not in signature


def test_get_procedure_body_returns_full_body_not_bare_keyword(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "Test.Codeunit.al",
        "codeunit 50100 MyCod\n"
        "{\n"
        "    local procedure Helper()\n"
        "    begin\n"
        "        Message('hi');\n"
        "    end;\n"
        "}\n",
    )

    body = get_procedure_body(p, "Line 3")

    assert body.strip() != "procedure"
    assert body.startswith("local procedure Helper()")
    assert "Message('hi');" in body
