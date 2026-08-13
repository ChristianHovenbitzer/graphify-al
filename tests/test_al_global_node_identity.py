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


# (d) #31: BC allows a table and a codeunit to share a bare name (e.g. the base
# app's Table 308 "No. Series" / Codeunit "No. Series"). Two out-of-corpus
# references that disagree on type -- a permission grant on the codeunit, a
# tableextension's base on the table -- must land on DISTINCT stubs, not merge
# into whichever one the extraction happened to visit first.
def test_type_colliding_external_refs_get_distinct_stubs(tmp_path: Path):
    result = _build(tmp_path, "app", {
        "MySet.PermissionSet.al": (
            'permissionset 50100 "My Set"\n'
            '{ Permissions = codeunit "No. Series" = X; }\n'
        ),
        "NoSeriesExt.TableExt.al": (
            'tableextension 50100 "No Series Ext" extends "No. Series"\n'
            '{ fields { field(50100; "Extra"; Integer) { } } }\n'
        ),
    })
    stubs = [n for n in _externals(result)
             if str(n.get("label", "")).strip('"').lower() == "no. series"]
    types = {n.get("al_object_type") for n in stubs}
    assert types == {"codeunit", "table"}, (
        f"expected distinct table/codeunit stubs for the shared bare name, got {stubs}"
    )
    ids = {n["id"] for n in stubs}
    assert len(ids) == 2, f"distinct types must not collapse to one stub id, got {ids}"
    gids = {n.get("global_id") for n in stubs}
    assert len(gids) == 2, f"distinct types must carry distinct global_ids, got {gids}"


# (e) #31: a call into an out-of-corpus object's specific procedure must mint a
# per-member stub (`Object.Method`), not collapse to an object-level stub --
# so the exact procedure referenced survives a cross-app/cross-graph boundary
# instead of being recoverable only by re-reading the caller's source by hand.
def test_call_into_external_object_mints_per_member_stub(tmp_path: Path):
    result = _build(tmp_path, "app", {
        "Test.Codeunit.al": (
            'codeunit 50100 "Test"\n'
            "{\n"
            "    var\n"
            '        NoSeriesMgt: Codeunit "No. Series";\n\n'
            "    procedure GenerateDocumentNo()\n"
            "    begin\n"
            "        NoSeriesMgt.GetNextNo('X', WorkDate());\n"
            "    end;\n"
            "}\n"
        ),
    })
    member_stubs = [n for n in _externals(result)
                    if str(n.get("label", "")).lower() == "no. series.getnextno"]
    assert len(member_stubs) == 1, f"expected one per-member stub, got {_externals(result)}"
    member_id = member_stubs[0]["id"]

    calls = [e for e in result["edges"] if e["relation"] == "calls"]
    assert any(e["target"] == member_id for e in calls), (
        "calls edge into the out-of-corpus procedure must target its per-member"
        f" stub, got targets {[e['target'] for e in calls]}"
    )
    # The object-level stub, if minted at all elsewhere, must stay a separate
    # node -- the calls edge must never land back on the whole-object stub.
    obj_stubs = [n for n in _externals(result)
                 if str(n.get("label", "")).strip('"').lower() == "no. series"]
    assert all(n["id"] != member_id for n in obj_stubs)


# (f) #33 follow-up to (e): the per-member stub minted for a call into an
# out-of-corpus object must carry `al_object_type` (known from the call
# site's own declared variable type) and a fully-typed `global_id`, not an
# empty type segment -- else two differently-typed objects sharing a bare
# name and a same-named method would collide on the same member stub.
def test_call_into_external_object_member_stub_is_typed(tmp_path: Path):
    result = _build(tmp_path, "app", {
        "Test.Codeunit.al": (
            'codeunit 50100 "Test"\n'
            "{\n"
            "    var\n"
            '        NoSeriesMgt: Codeunit "No. Series";\n\n'
            "    procedure GenerateDocumentNo()\n"
            "    begin\n"
            "        NoSeriesMgt.GetNextNo('X', WorkDate());\n"
            "    end;\n"
            "}\n"
        ),
    })
    member_stub = next(
        n for n in _externals(result)
        if str(n.get("label", "")).lower() == "no. series.getnextno"
    )
    assert member_stub.get("al_object_type") == "codeunit"
    assert member_stub.get("global_id") == "al:///codeunit/no. series.getnextno"


# (g) #33: same typing fix for a subscribes edge onto an out-of-corpus
# publisher -- the `[EventSubscriber(ObjectType::X, ...)]` attribute's own
# publisher type carries onto the per-member stub, exactly mirroring (f).
def test_subscribe_to_external_publisher_member_stub_is_typed(tmp_path: Path):
    result = _build(tmp_path, "app", {
        "Test.Codeunit.al": (
            'codeunit 50100 "Test"\n'
            "{\n"
            '    [EventSubscriber(ObjectType::Codeunit, Codeunit::"Sales-Post",'
            " 'OnAfterPostSalesDoc', '', false, false)]\n"
            "    local procedure OnAfterPostSalesDoc()\n"
            "    begin\n"
            "    end;\n"
            "}\n"
        ),
    })
    member_stub = next(
        n for n in _externals(result)
        if str(n.get("label", "")).lower() == "sales-post.onafterpostsalesdoc"
    )
    assert member_stub.get("al_object_type") == "codeunit"
    assert member_stub.get("global_id") == "al:///codeunit/sales-post.onafterpostsalesdoc"
