from __future__ import annotations

from pathlib import Path

from graphify.extract import extract


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _build(tmp_path: Path, name: str, files: dict[str, str]) -> dict:
    """Extract an isolated corpus (its own cache) from {filename: source}."""
    root = tmp_path / name / "src"
    paths = [_write(root / fn, txt) for fn, txt in files.items()]
    return extract(paths, cache_root=tmp_path / name / "cache")


def _obj_nodes(result: dict) -> list[dict]:
    out = []
    for n in result["nodes"]:
        if not str(n.get("source_file", "")).lower().endswith(".al"):
            continue
        lbl = n.get("label", "")
        if lbl.endswith(".al") or lbl.startswith("."):
            continue
        out.append(n)
    return out


def _by_name(result: dict, name: str) -> dict:
    # Real object nodes carry the bare name separately in `al_object_name`
    # (`label` is now the full type+ID+name canonical reference, spec
    # 004-al-object-labels).
    for n in _obj_nodes(result):
        if str(n.get("al_object_name", "")).lower() == name.lower():
            return n
    raise AssertionError(f"object {name!r} not found")


def _externals(result: dict) -> list[dict]:
    return [n for n in result["nodes"] if n.get("file_type") == "external"]


# (a) The federation invariant: a stub for an external object built in an
# EXTENDING app must carry the same global_id as the REAL object node built
# in its own corpus — that equality is the deterministic cross-graph join key.
def test_stub_and_real_object_share_global_id(tmp_path: Path):
    extending = _build(tmp_path, "app", {
        "GadgetExt.TableExt.al": (
            "namespace Contoso.App;\n"
            'tableextension 50100 "Gadget Ext" extends "Gadget"\n'
            "{ fields { field(50100; \"Extra\"; Integer) { } } }\n"
        ),
    })
    base = _build(tmp_path, "base", {
        "Gadget.Table.al": (
            "namespace Contoso.App;\n"
            'table 50000 "Gadget"\n'
            '{ fields { field(1; "No."; Code[20]) { } } }\n'
        ),
    })

    stubs = [n for n in _externals(extending)
             if str(n.get("label", "")).strip('"').lower() == "gadget"]
    assert len(stubs) == 1, f"expected one external stub for Gadget, got {stubs}"
    stub = stubs[0]
    real = _by_name(base, "Gadget")

    assert stub.get("global_id"), "stub is missing a global_id"
    assert real.get("global_id"), "real object is missing a global_id"
    # The whole point of #27: independently-built graphs join on this key.
    assert stub["global_id"] == real["global_id"]
    # Object type is part of the key (the base of a tableextension is a table).
    assert real["global_id"] == "al://contoso.app/table/gadget"


# (b) Object type is part of the key, so a table and a same-named page — which
# collide on bare name — get DISTINCT global_ids.
def test_same_name_table_and_page_have_distinct_global_ids(tmp_path: Path):
    result = _build(tmp_path, "c", {
        "Widget.Table.al": (
            "namespace Demo.Sales;\n"
            'table 50100 "Widget" { fields { field(1; "No."; Code[20]) { } } }\n'
        ),
        "Widget.Page.al": (
            "namespace Demo.Sales;\n"
            'page 50101 "Widget" { layout { area(content) { } } }\n'
        ),
    })
    table = next(n for n in _obj_nodes(result) if n.get("al_object_type") == "table")
    page = next(n for n in _obj_nodes(result) if n.get("al_object_type") == "page")

    assert table["global_id"] == "al://demo.sales/table/widget"
    assert page["global_id"] == "al://demo.sales/page/widget"
    assert table["global_id"] != page["global_id"]


# (c) The same external object referenced through DIFFERENT facts (an extends
# base and a field TableRelation) resolves to external stub(s) that all carry
# ONE global_id — dedup on the global key, regardless of the reference path.
def test_multiple_references_to_one_external_object_share_global_id(tmp_path: Path):
    app = _build(tmp_path, "app", {
        "CustExt.TableExt.al": (
            "namespace Contoso.App;\n"
            'tableextension 50100 "Cust Ext" extends "Customer"\n'
            "{ fields { field(50100; \"Extra\"; Integer) { } } }\n"
        ),
        "Order.Table.al": (
            "namespace Contoso.App;\n"
            'table 50101 "My Order"\n'
            "{ fields { field(1; \"Cust\"; Code[20]) { TableRelation = \"Customer\"; } } }\n"
        ),
    })
    cust_stubs = [n for n in _externals(app)
                  if str(n.get("label", "")).strip('"').lower() == "customer"]
    assert cust_stubs, "expected an external stub for Customer"
    gids = {n.get("global_id") for n in cust_stubs}
    assert len(gids) == 1, f"one external object must map to one global_id, got {gids}"
    assert gids == {"al://contoso.app/table/customer"}

    # And it still matches the real Customer built in its own corpus.
    base = _build(tmp_path, "base", {
        "Customer.Table.al": (
            "namespace Contoso.App;\n"
            'table 18 "Customer" { fields { field(1; "No."; Code[20]) { } } }\n'
        ),
    })
    assert next(iter(gids)) == _by_name(base, "Customer")["global_id"]


# app.json publisher/name is the fallback qualifier when a file declares no
# namespace; the key must still be emitted (degrade gracefully, never crash).
def test_global_id_falls_back_to_app_json_without_namespace(tmp_path: Path):
    root = tmp_path / "app"
    _write(root / "app.json",
           '{"id": "aaaaaaaa-0000-0000-0000-000000000001",'
           ' "name": "Widgets", "publisher": "Contoso", "version": "1.0.0.0"}')
    f = _write(root / "src" / "Legacy.Codeunit.al",
               'codeunit 50100 "Legacy Mgt" { }\n')
    result = extract([f], cache_root=tmp_path / "cache")
    node = _by_name(result, "Legacy Mgt")
    assert node["global_id"] == "al://app:contoso::widgets/codeunit/legacy mgt"


def test_global_id_empty_qualifier_when_no_namespace_and_no_app_json(tmp_path: Path):
    f = _write(tmp_path / "src" / "Bare.Codeunit.al",
               'codeunit 50100 "Bare Mgt" { }\n')
    result = extract([f], cache_root=tmp_path / "cache")
    node = _by_name(result, "Bare Mgt")
    # No namespace and no app.json -> empty qualifier, but still a valid key.
    assert node["global_id"] == "al:///codeunit/bare mgt"
