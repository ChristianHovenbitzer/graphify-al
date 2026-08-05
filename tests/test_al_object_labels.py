"""AL object node labels include type and numeric ID (spec 004-al-object-labels).

Fixes a real bug traced to upstream commit 05cbe56 ("Add AL / Business
Central language support"): `_extract_generic`'s class-name resolution
falls through to the bare `object_name` field for AL objects, dropping the
object type keyword and numeric ID that the grammar exposes as separate
fields (`object_id`, `object_name`) -- reproduced live: querying
`bcatlas_get_object_source` with the natural, canonical AL reference
`Codeunit 12 "Gen. Jnl.-Post Line"` returned "No node matching found"
because the indexed label was only `"Gen. Jnl.-Post Line"`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_al")

from graphify.extract import _AL_CONFIG, extract_al  # noqa: E402


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _obj_node(result: dict) -> dict:
    for n in result["nodes"]:
        lbl = n.get("label", "")
        if not (lbl.endswith(".al") or lbl.startswith(".")):
            return n
    raise AssertionError("no object node found")


# --- R1's grounding check: which AL declaration kinds expose `object_id`? ---
# Canary for tree-sitter-al grammar drift -- if a future grammar version
# changes this, this test fails loudly here instead of surfacing as a
# confusing label regression downstream.
@pytest.mark.parametrize(
    "source,expects_id",
    [
        ('codeunit 12 "Gen. Jnl.-Post Line"\n{\n}\n', True),
        ('table 27 "Item"\n{\n}\n', True),
        ('page 21 "Item Card"\n{\n}\n', True),
        ('report 5 "Item List"\n{\n}\n', True),
        ('query 50100 "My Query"\n{\n}\n', True),
        ('xmlport 50101 "My Port"\n{\n}\n', True),
        ('enum 50102 "My Enum"\n{\n}\n', True),
        ('tableextension 50103 "My Ext" extends "Item"\n{\n}\n', True),
        ('interface "My Iface"\n{\n}\n', False),
        ('controladdin PdfViewer\n{\n}\n', False),
    ],
)
def test_object_id_field_presence_matches_grammar_expectation(
    tmp_path: Path, source: str, expects_id: bool
) -> None:
    import tree_sitter_al as tsal
    from tree_sitter import Language, Parser

    parser = Parser(Language(tsal.language()))
    tree = parser.parse(source.encode("utf-8"))

    def find_decl(n):
        if n.type in _AL_CONFIG.class_types:
            return n
        for c in n.children:
            found = find_decl(c)
            if found is not None:
                return found
        return None

    decl = find_decl(tree.root_node)
    assert decl is not None, f"no object declaration found in {source!r}"
    has_id = decl.child_by_field_name("object_id") is not None
    assert has_id == expects_id, (
        f"{decl.type} object_id presence changed: expected {expects_id}, got {has_id} "
        "-- tree-sitter-al grammar may have changed; re-verify the fix's assumptions"
    )


# --- FR-001/FR-002/FR-003: label format ---

def test_codeunit_label_includes_type_and_id(tmp_path: Path) -> None:
    p = _write(tmp_path, "GenJnlPostLine.Codeunit.al",
               'codeunit 12 "Gen. Jnl.-Post Line"\n{\n}\n')
    result = extract_al(p)
    obj = _obj_node(result)
    assert obj["label"] == 'Codeunit 12 "Gen. Jnl.-Post Line"'
    assert obj["al_object_type"] == "codeunit"
    assert obj["al_object_name"] == "Gen. Jnl.-Post Line"


def test_tableextension_label_includes_type_and_id(tmp_path: Path) -> None:
    p = _write(tmp_path, "MyExt.TableExt.al",
               'tableextension 50100 "My Ext" extends "Item"\n{\n}\n')
    result = extract_al(p)
    obj = _obj_node(result)
    assert obj["label"] == 'TableExtension 50100 "My Ext"'


def test_interface_label_has_no_placeholder_id(tmp_path: Path) -> None:
    p = _write(tmp_path, "MyIface.Interface.al", 'interface "My Iface"\n{\n}\n')
    result = extract_al(p)
    obj = _obj_node(result)
    assert obj["label"] == 'Interface "My Iface"'


def test_controladdin_label_has_no_placeholder_id(tmp_path: Path) -> None:
    p = _write(tmp_path, "PdfViewer.al", "controladdin PdfViewer\n{\n}\n")
    result = extract_al(p)
    obj = _obj_node(result)
    assert obj["label"] == 'ControlAddIn "PdfViewer"'


def test_node_id_and_edges_unaffected_by_label_change(tmp_path: Path) -> None:
    """Critical invariant (research.md R1): node IDs are computed from the
    bare name during the generic pass, before this AL-specific label
    rewrite runs -- the label correction must never change node identity.
    """
    p = _write(tmp_path, "GenJnlPostLine.Codeunit.al", (
        'codeunit 12 "Gen. Jnl.-Post Line"\n'
        "{\n"
        "    procedure DoSomething()\n"
        "    begin\n"
        "    end;\n"
        "}\n"
    ))
    result = extract_al(p)
    obj = _obj_node(result)
    # Node id derives from the bare name, unaffected by the label rewrite.
    assert "gen" in obj["id"] and "jnl" in obj["id"]
    contains = {(e["source"], e["target"]) for e in result["edges"] if e["relation"] == "method"}
    proc = next(n for n in result["nodes"] if n["label"] == ".DoSomething()")
    assert (obj["id"], proc["id"]) in contains
