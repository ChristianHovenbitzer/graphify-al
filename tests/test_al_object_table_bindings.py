"""Object→table `binds` edges: page SourceTable and report/query/xmlport dataitem.

The generic extractor sees the object and its procedures but not the data binding
that ties a page/report/query/xmlport to its table. `_al_collect_facts` now emits a
`binds` fact for these, resolved in `_resolve_al_facts` to an in-corpus table node
or, for a target outside the corpus, an external stub (mirroring `calls`).

Like the existing `extends` edge, the binding is attributed to the binding object's
declaration line, which resolves to that object's file node as the edge source; the
target is the resolved table (or external stub).
"""

from graphify.extract import extract


def _binds(edges):
    return [e for e in edges if e["relation"] == "binds"]


def _node_by_label(nodes, label):
    return next(n for n in nodes if str(n.get("label", "")).strip('"') == label)


def test_page_sourcetable_binds_to_in_corpus_table(tmp_path):
    (tmp_path / "entity.al").write_text(
        'table 50110 "My Entity"\n{\n    fields { field(1; "Name"; Text[50]) { } }\n}\n',
        encoding="utf-8",
    )
    (tmp_path / "card.al").write_text(
        'page 50100 "My Entity Card"\n{\n    PageType = Card;\n    SourceTable = "My Entity";\n}\n',
        encoding="utf-8",
    )

    result = extract([tmp_path / "entity.al", tmp_path / "card.al"], cache_root=tmp_path)
    page_file = _node_by_label(result["nodes"], "card.al")
    table = _node_by_label(result["nodes"], "My Entity")

    binds = _binds(result["edges"])
    edge = next(
        (e for e in binds if e["source"] == page_file["id"] and e["target"] == table["id"]),
        None,
    )
    assert edge is not None, binds
    assert edge["confidence"] == "EXTRACTED"
    assert edge["context"] == "al_binds"


def test_report_dataitem_binds_out_of_corpus_target_as_external_stub(tmp_path):
    (tmp_path / "rep.al").write_text(
        'report 50101 "My Report"\n{\n'
        "    dataset\n    {\n"
        '        dataitem(Line; "My Ledger Entry") { }\n'
        "    }\n}\n",
        encoding="utf-8",
    )

    result = extract([tmp_path / "rep.al"], cache_root=tmp_path)
    report_file = _node_by_label(result["nodes"], "rep.al")

    stub = _node_by_label(result["nodes"], "My Ledger Entry")
    assert stub["file_type"] == "external"

    binds = _binds(result["edges"])
    assert any(
        e["source"] == report_file["id"] and e["target"] == stub["id"] for e in binds
    ), binds


def test_query_and_xmlport_dataitem_bind_to_tables(tmp_path):
    (tmp_path / "tbl.al").write_text(
        'table 50120 "My Line" { fields { field(1; "Entry No."; Integer) { } } }\n',
        encoding="utf-8",
    )
    (tmp_path / "q.al").write_text(
        'query 50102 "My Query"\n{\n    elements { dataitem(L; "My Line") { } }\n}\n',
        encoding="utf-8",
    )
    (tmp_path / "port.al").write_text(
        'xmlport 50103 "My Port"\n{\n'
        "    schema\n    {\n"
        "        textelement(Root)\n        {\n"
        '            tableelement(El; "My Line") { }\n'
        "        }\n    }\n}\n",
        encoding="utf-8",
    )

    result = extract(
        [tmp_path / "tbl.al", tmp_path / "q.al", tmp_path / "port.al"], cache_root=tmp_path
    )
    table = _node_by_label(result["nodes"], "My Line")
    query_file = _node_by_label(result["nodes"], "q.al")
    port_file = _node_by_label(result["nodes"], "port.al")

    binds = _binds(result["edges"])
    assert any(
        e["source"] == query_file["id"] and e["target"] == table["id"] for e in binds
    ), binds
    assert any(
        e["source"] == port_file["id"] and e["target"] == table["id"] for e in binds
    ), binds
