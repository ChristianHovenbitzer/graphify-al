"""AL PermissionSet -> granted-object permission edge extraction (#31).

A permissionset's `Permissions` property grants a mask (RIMD / X / ...) on each
listed object. These tests cover the fact-level parse (object kind + mask for
tabledata/page/report/codeunit/query/xmlport/system), in-corpus target resolution
with the mask carried on the edge, the external-stub fallback for out-of-corpus
targets, and permissionsetextension added permissions. Fixtures are fully
synthetic (invented 50xxx objects / standard BC object names).
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract  # noqa: E402


def _grant_edges(result):
    return [e for e in result["edges"] if e["relation"] == "grants"]


def test_collect_facts_object_kinds_and_masks():
    src = b'''permissionset 50100 "My Set" {
    Assignable = true;
    Permissions = tabledata Customer = RIMD,
                  tabledata "Sales Header" = rimd,
                  page "Customer Card" = X,
                  report "My Report" = X,
                  codeunit "Sales-Post" = X,
                  query MyQuery = X,
                  xmlport MyPort = X,
                  system "Tools, Debugger" = X;
}
'''
    import tree_sitter_al as tsal
    from tree_sitter import Language, Parser
    from graphify.extract import _al_collect_facts

    tree = Parser(Language(tsal.language())).parse(src)
    facts = [f for f in _al_collect_facts(tree, src) if f["kind"] == "grants"]

    got = sorted((f["obj_kind"], f["target"], f["mask"]) for f in facts)
    assert got == sorted([
        ("codeunit", "Sales-Post", "X"),
        ("page", "Customer Card", "X"),
        ("query", "MyQuery", "X"),
        ("report", "My Report", "X"),
        ("system", "Tools, Debugger", "X"),
        ("tabledata", "Customer", "RIMD"),
        ("tabledata", "Sales Header", "RIMD"),  # lowercase mask normalized
        ("xmlport", "MyPort", "X"),
    ])


def test_grants_edge_resolves_to_target_node_with_mask(tmp_path):
    pset = tmp_path / "MySet.PermissionSet.al"
    tbl = tmp_path / "MyOrder.Table.al"
    pset.write_text(
        'permissionset 50100 "My Set" {\n'
        '    Permissions = tabledata "My Order" = RIMD;\n'
        '}\n',
        encoding="utf-8",
    )
    tbl.write_text(
        'table 50101 "My Order" {\n'
        '    fields {\n'
        '        field(1; "No."; Code[20]) { }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([pset, tbl], cache_root=tmp_path)

    order_obj = next(n["id"] for n in result["nodes"] if n["label"] == '"My Order"')
    pset_src = next(e["source"] for e in _grant_edges(result))

    grants = _grant_edges(result)
    assert len(grants) == 1
    edge = grants[0]
    assert edge["source_file"].endswith("MySet.PermissionSet.al")
    assert edge["target"] == order_obj
    assert edge["mask"] == "RIMD"
    assert edge["al_object_kind"] == "tabledata"
    assert edge["confidence"] == "EXTRACTED"
    assert edge["context"] == "al_grants"
    # source is the permissionset object node, not a stray file node.
    assert pset_src == edge["source"]


def test_grants_unresolved_target_becomes_external_stub(tmp_path):
    pset = tmp_path / "MySet.PermissionSet.al"
    pset.write_text(
        'permissionset 50100 "My Set" {\n'
        '    Permissions = tabledata Customer = RIMD, page "Customer Card" = X;\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([pset], cache_root=tmp_path)

    grants = _grant_edges(result)
    assert len(grants) == 2
    by_mask = {e["mask"]: e for e in grants}

    cust_stub = next(n for n in result["nodes"] if n["id"] == by_mask["RIMD"]["target"])
    assert cust_stub["file_type"] == "external"
    assert cust_stub["label"] == "Customer"
    assert cust_stub["al_object_type"] == "table"

    page_stub = next(n for n in result["nodes"] if n["id"] == by_mask["X"]["target"])
    assert page_stub["file_type"] == "external"
    assert page_stub["label"] == "Customer Card"
    assert page_stub["al_object_type"] == "page"


def test_permissionsetextension_added_permissions(tmp_path):
    ext = tmp_path / "MySetExt.PermissionSetExt.al"
    ext.write_text(
        'permissionsetextension 50100 "My Set Ext" extends "D365 BASIC" {\n'
        '    Permissions = tabledata Vendor = RIMD, page "Vendor Card" = X;\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([ext], cache_root=tmp_path)

    grants = _grant_edges(result)
    got = sorted((e["al_object_kind"], e["mask"]) for e in grants)
    assert got == [("page", "X"), ("tabledata", "RIMD")]
    # both grants originate from the same permissionsetextension object node.
    assert len({e["source"] for e in grants}) == 1
