from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_al")

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _label(result: dict, nid: str) -> str:
    for n in result["nodes"]:
        if n["id"] == nid:
            return n.get("label", "")
    return f"<{nid}>"


def _edge_labels(result: dict, relations) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()
    for e in result["edges"]:
        if e.get("relation") in relations:
            out.add((_label(result, e["source"]), e["relation"], _label(result, e["target"])))
    return out


def _al_fixture(base: Path) -> list[Path]:
    iface = _write(base / "IPay.al",
                   "interface IPay\n{\n    procedure Charge();\n}\n")
    impl = _write(base / "PayCard.al",
                  "codeunit 50100 PayCard implements IPay\n{\n"
                  "    procedure Charge() begin end;\n}\n")
    paypal = _write(base / "PayPalImpl.al",
                    "codeunit 50102 PayPalImpl implements IPay\n{\n"
                    "    procedure Charge() begin end;\n}\n")
    enum = _write(base / "PayMethod.al",
                  "enum 50101 PayMethod implements IPay\n{\n"
                  "    value(0; PayPal) { Implementation = IPay = PayPalImpl; }\n"
                  "}\n")
    return [iface, impl, paypal, enum]


def test_al_implements_and_enum_bound_implementation_edges(tmp_path: Path):
    files = _al_fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    # The interface object is a node in its own right.
    assert "IPay" in {n.get("label") for n in result["nodes"]}

    impl_edges = _edge_labels(result, ("implements",))
    bind_edges = _edge_labels(result, ("enum_binds_implementation",))

    # 1. codeunit ... implements IFoo
    assert ("PayCard", "implements", "IPay") in impl_edges
    # 2a. enum binds the concrete impl codeunit
    assert ("PayMethod", "enum_binds_implementation", "PayPalImpl") in bind_edges
    # 2b. the enum-bound impl implements the interface
    assert ("PayPalImpl", "implements", "IPay") in impl_edges
    # 2c. the enum's own implements clause
    assert ("PayMethod", "implements", "IPay") in impl_edges


def test_al_implements_edges_are_extracted_confidence(tmp_path: Path):
    files = _al_fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    for e in result["edges"]:
        if e.get("relation") in ("implements", "enum_binds_implementation"):
            assert e["confidence"] == "EXTRACTED"
