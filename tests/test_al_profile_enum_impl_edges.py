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


def _fixture(base: Path) -> list[Path]:
    iface = _write(base / "IPay.al",
                   "interface IPay\n{\n    procedure Charge();\n}\n")
    default_impl = _write(base / "DefaultPay.al",
                          "codeunit 50200 DefaultPay implements IPay\n{\n"
                          "    procedure Charge() begin end;\n}\n")
    unknown_impl = _write(base / "FallbackPay.al",
                          "codeunit 50201 FallbackPay implements IPay\n{\n"
                          "    procedure Charge() begin end;\n}\n")
    card_impl = _write(base / "CardPay.al",
                       "codeunit 50202 CardPay implements IPay\n{\n"
                       "    procedure Charge() begin end;\n}\n")
    role_center = _write(base / "SalesRC.al",
                         'page 50210 "Sales Manager Role Center"\n{\n'
                         "    PageType = RoleCenter;\n}\n")
    enum = _write(base / "Pay.al",
                  "enum 50203 Pay implements IPay\n{\n"
                  "    DefaultImplementation = IPay = DefaultPay;\n"
                  "    UnknownValueImplementation = IPay = FallbackPay;\n"
                  "    value(0; Card) { Implementation = IPay = CardPay; }\n"
                  "}\n")
    profile = _write(base / "SalesAgent.al",
                     'profile "Sales Agent"\n{\n'
                     '    Caption = \'Sales Agent\';\n'
                     '    RoleCenter = "Sales Manager Role Center";\n}\n')
    return [iface, default_impl, unknown_impl, card_impl, role_center, enum, profile]


def test_profile_rolecenter_edge(tmp_path: Path):
    files = _fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    rc_edges = _edge_labels(result, ("rolecenter",))
    # quoted-identifier objects keep their quotes in the node label.
    assert ('"Sales Agent"', "rolecenter", '"Sales Manager Role Center"') in rc_edges


def test_enum_object_level_default_and_unknown_implementation_edges(tmp_path: Path):
    files = _fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    bind_edges = _edge_labels(result, ("enum_binds_implementation",))
    impl_edges = _edge_labels(result, ("implements",))

    # object-level DefaultImplementation / UnknownValueImplementation anchor on
    # the enum object itself.
    assert ("Pay", "enum_binds_implementation", "DefaultPay") in bind_edges
    assert ("Pay", "enum_binds_implementation", "FallbackPay") in bind_edges
    # each bound impl implements the interface.
    assert ("DefaultPay", "implements", "IPay") in impl_edges
    assert ("FallbackPay", "implements", "IPay") in impl_edges


def test_enum_value_level_implementation_anchors_on_value_node(tmp_path: Path):
    files = _fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    bind_edges = _edge_labels(result, ("enum_binds_implementation",))
    # value-level Implementation anchors on the enum VALUE node (".Card"), not the
    # enum object.
    assert (".Card", "enum_binds_implementation", "CardPay") in bind_edges
    assert ("Pay", "enum_binds_implementation", "CardPay") not in bind_edges


def test_new_al_edges_are_extracted_confidence(tmp_path: Path):
    files = _fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    for e in result["edges"]:
        if e.get("relation") in ("rolecenter", "enum_binds_implementation"):
            assert e["confidence"] == "EXTRACTED"
