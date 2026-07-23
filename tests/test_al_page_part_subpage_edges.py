"""AL page `part(...)` / SubPage / SubPageLink composition edges (#30).

A page/factbox `part(<Name>; <TargetPage>)` composes a subpage: an edge from the
part member (host page) to the target page, carrying the `SubPageLink` linkage
when present. The part becomes its own member node, so the edge attaches to that
member (not the whole host page), and an out-of-corpus target falls back to an
external stub like the other object references.

Fixtures are fully synthetic: standard BC table names (Item) and invented 50xxx
objects only.
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract, extract_al  # noqa: E402


def _subpage_edges(result):
    return [e for e in result["edges"] if e["relation"] == "subpage"]


def test_collect_facts_part_target_and_subpagelink():
    src = b'''page 50100 "Widget Card" {
    layout {
        area(factboxes) {
            part(Details; "Widget Detail FactBox") {
                SubPageLink = "No." = field("No.");
            }
            part(Lines; "Widget Line Part") { }
        }
    }
}
'''
    import tree_sitter_al as tsal
    from tree_sitter import Language, Parser
    from graphify.extract import _al_collect_facts

    tree = Parser(Language(tsal.language())).parse(src)
    facts = [f for f in _al_collect_facts(tree, src) if f["kind"] == "sub_page"]

    by_target = {f["target"]: f for f in facts}
    assert set(by_target) == {"Widget Detail FactBox", "Widget Line Part"}
    # SubPageLink linkage captured (whitespace-normalized); absent when not given.
    assert by_target["Widget Detail FactBox"]["sub_page_link"] == '"No." = field("No.")'
    assert "sub_page_link" not in by_target["Widget Line Part"]


def test_subpage_edge_resolves_to_target_page_and_attaches_to_part_member(tmp_path):
    host = tmp_path / "WidgetCard.Page.al"
    target = tmp_path / "WidgetDetailFactBox.Page.al"
    host.write_text(
        'page 50100 "Widget Card" {\n'
        '    layout {\n'
        '        area(factboxes) {\n'
        '            part(Details; "Widget Detail FactBox") {\n'
        '                SubPageLink = "No." = field("No.");\n'
        '            }\n'
        '        }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )
    target.write_text(
        'page 50101 "Widget Detail FactBox" {\n'
        '    SourceTable = Item;\n'
        '    layout { area(content) { field(No; Rec."No.") { } } }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([host, target], cache_root=tmp_path)

    part_member = next(n["id"] for n in result["nodes"] if n["label"] == ".Details")
    target_page = next(n["id"] for n in result["nodes"]
                       if n["label"] == '"Widget Detail FactBox"')

    edges = _subpage_edges(result)
    assert len(edges) == 1
    edge = edges[0]
    # Attaches to the part member node, points at the target page.
    assert edge["source"] == part_member
    assert edge["target"] == target_page
    assert edge["sub_page_link"] == '"No." = field("No.")'
    assert edge["confidence"] == "EXTRACTED"
    assert edge["context"] == "al_sub_page"
    assert edge["source_file"].endswith("WidgetCard.Page.al")


def test_subpage_unresolved_target_becomes_external_stub(tmp_path):
    host = tmp_path / "WidgetCard.Page.al"
    host.write_text(
        'page 50100 "Widget Card" {\n'
        '    layout {\n'
        '        area(factboxes) {\n'
        '            part(Notes; "My Notes FactBox") { }\n'
        '        }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([host], cache_root=tmp_path)

    edges = _subpage_edges(result)
    assert len(edges) == 1
    stub = next(n for n in result["nodes"] if n["id"] == edges[0]["target"])
    assert stub["file_type"] == "external"
    assert stub["label"] == "My Notes FactBox"
    assert stub.get("al_object_type") == "page"
