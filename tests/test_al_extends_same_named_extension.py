from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _obj_nodes(result: dict) -> list[dict]:
    """AL object nodes (skip .al file nodes and .proc() nodes)."""
    out = []
    for n in result["nodes"]:
        sf = str(n.get("source_file", ""))
        lbl = n.get("label", "")
        if not sf.lower().endswith(".al"):
            continue
        if lbl.endswith(".al") or lbl.startswith("."):
            continue
        out.append(n)
    return out


def _extends_edges(result: dict) -> list[dict]:
    return [e for e in result["edges"] if e.get("relation") == "extends"]


BASE_TABLE = (
    "namespace Demo.App;\n"
    "\n"
    'table 50100 "Gadget"\n'
    "{\n"
    "    fields\n"
    "    {\n"
    '        field(1; "No."; Code[20]) { }\n'
    "    }\n"
    "}\n"
)

# An extension shares the base object's identifier — the crux of #10.
EXT_TABLE = (
    "namespace Demo.App;\n"
    "\n"
    'tableextension 50101 "Gadget" extends "Gadget"\n'
    "{\n"
    "    fields\n"
    "    {\n"
    '        field(50100; "Extra Info"; Integer) { }\n'
    "    }\n"
    "}\n"
)


def test_extends_edge_survives_same_named_base(tmp_path: Path):
    # #10: `tableextension 50101 Gadget extends Gadget` must still yield an
    # extends edge. Name resolution collides the extension with its base, so the
    # src == tgt self-guard used to drop it.
    src = tmp_path / "src"
    base_f = _write(src / "Gadget.Table.al", BASE_TABLE)
    ext_f = _write(src / "GadgetExt.TableExt.al", EXT_TABLE)
    # Extension processed first, so bare-name resolution collides onto the
    # extension's own node — the exact ordering that made the self-guard drop
    # the edge before the fix.
    result = extract([ext_f, base_f], cache_root=tmp_path / "cache")

    edges = _extends_edges(result)
    assert len(edges) == 1, f"expected one extends edge, got {edges}"
    edge = edges[0]

    # Not a dropped edge, and not a self-loop: the extension node and the base
    # node are distinct (different file stems -> different ids).
    assert edge["source"] != edge["target"]

    by_id = {n["id"]: n for n in result["nodes"]}
    src_sf = str(by_id[edge["source"]]["source_file"])
    tgt_sf = str(by_id[edge["target"]]["source_file"])
    assert src_sf.endswith("GadgetExt.TableExt.al")  # from the extension
    assert tgt_sf.endswith("Gadget.Table.al")        # to the real base table

    # Both object nodes exist and share the label; the fix keys off ids, not names.
    assert len(_obj_nodes(result)) == 2


def test_extends_edge_to_out_of_corpus_base_is_external_not_self_loop(tmp_path: Path):
    # When the base lives outside the corpus (standard BC object), the only
    # same-named node is the extension itself. The edge must point at an external
    # stub, never loop back onto the extension (which the self-guard would drop).
    src = tmp_path / "src"
    ext_f = _write(src / "GadgetExt.TableExt.al", EXT_TABLE)
    result = extract([ext_f], cache_root=tmp_path / "cache")

    edges = _extends_edges(result)
    assert len(edges) == 1, f"expected one extends edge, got {edges}"
    edge = edges[0]
    assert edge["source"] != edge["target"]

    by_id = {n["id"]: n for n in result["nodes"]}
    assert by_id[edge["target"]].get("file_type") == "external"
