"""AL page `action` RunObject / RunPageLink navigation edge extraction (#28).

A page/pageextension `action(...)` opens another object via `RunObject` and may
filter it via `RunPageLink`. These tests cover:
  * `navigates_to` from the object+member-qualified action node to the RunObject
    target (in-corpus page/report node, or an external stub carrying its kind),
  * `links_field` from that same action node to the source page's own SourceTable
    field named in a `RunPageLink ... = field("...")` pairing (const/filter
    literals are not field links),
  * both edges landing on the correct action member node, not the page object.
Fixtures are fully synthetic (invented 50xxx objects).
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract  # noqa: E402


def _facts(src: bytes):
    import tree_sitter_al as tsal
    from tree_sitter import Language, Parser
    from graphify.extract import _al_collect_facts

    tree = Parser(Language(tsal.language())).parse(src)
    return _al_collect_facts(tree, src)


def test_collect_facts_runobject_and_runpagelink():
    src = b'''page 50100 "Order List"
{
    SourceTable = "My Order";
    actions
    {
        area(processing)
        {
            action(Entries)
            {
                RunObject = page "My Order Entries";
                RunPageLink = "Order No." = field("No."),
                              "Entry Type" = const(0);
            }
            action(PrintReport)
            {
                RunObject = report "My Order Report";
            }
        }
    }
}
'''
    facts = _facts(src)
    nav = sorted((f["target"], f["target_kind"]) for f in facts
                 if f["kind"] == "navigates_to")
    assert nav == [("My Order Entries", "page"), ("My Order Report", "report")]

    # RunPageLink field(...) links the source field; const(...) is ignored.
    links = [(f["target"], f["field"]) for f in facts if f["kind"] == "links_field"]
    assert links == [("My Order", "No.")]


def test_navigation_edges_resolve_to_member_and_target_nodes(tmp_path):
    lst = tmp_path / "OrderList.Page.al"
    lst.write_text(
        'page 50100 "Order List"\n'
        '{\n'
        '    SourceTable = "My Order";\n'
        '    actions\n'
        '    {\n'
        '        area(processing)\n'
        '        {\n'
        '            action(Entries)\n'
        '            {\n'
        '                RunObject = page "My Order Entries";\n'
        '                RunPageLink = "Order No." = field("No.");\n'
        '            }\n'
        '        }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )
    entries = tmp_path / "OrderEntries.Page.al"
    entries.write_text(
        'page 50101 "My Order Entries" { SourceTable = "My Order Entry"; }\n',
        encoding="utf-8",
    )
    order = tmp_path / "Order.Table.al"
    order.write_text(
        'table 50110 "My Order"\n'
        '{\n'
        '    fields { field(1; "No."; Code[20]) { } }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([lst, entries, order], cache_root=tmp_path)

    action_nid = next(n["id"] for n in result["nodes"] if n["label"] == ".Entries")
    entries_obj = next(n["id"] for n in result["nodes"]
                       if n["label"] == '"My Order Entries"')
    no_field = next(n["id"] for n in result["nodes"] if n["label"] == '."No."')

    nav = [e for e in result["edges"] if e["relation"] == "navigates_to"]
    assert len(nav) == 1
    assert nav[0]["source"] == action_nid          # the action member node, not the page
    assert nav[0]["target"] == entries_obj
    assert nav[0]["confidence"] == "EXTRACTED"
    assert nav[0]["context"] == "al_navigates_to"

    links = [e for e in result["edges"] if e["relation"] == "links_field"]
    assert len(links) == 1
    assert links[0]["source"] == action_nid
    assert links[0]["target"] == no_field          # source SourceTable field node
    assert links[0]["context"] == "al_links_field"


def test_runobject_unresolved_target_becomes_typed_external_stub(tmp_path):
    lst = tmp_path / "OrderList.Page.al"
    lst.write_text(
        'page 50100 "Order List"\n'
        '{\n'
        '    actions\n'
        '    {\n'
        '        area(processing)\n'
        '        {\n'
        '            action(Card) { RunObject = page "Order Card"; }\n'
        '        }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([lst], cache_root=tmp_path)

    nav = [e for e in result["edges"] if e["relation"] == "navigates_to"]
    assert len(nav) == 1
    stub = next(n for n in result["nodes"] if n["id"] == nav[0]["target"])
    assert stub["file_type"] == "external"
    assert stub["label"] == "Order Card"
    assert stub["al_object_type"] == "page"
