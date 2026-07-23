from __future__ import annotations

from pathlib import Path

from graphify.build import build_from_json
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


def _edges(result: dict, relation: str) -> list[dict]:
    return [e for e in result["edges"] if e.get("relation") == relation]


def _lineage(result: dict) -> set[tuple[str, str]]:
    """(source member label, target field label) pairs of sources_field edges."""
    return {
        (_label(result, e["source"]), _label(result, e["target"]))
        for e in _edges(result, "sources_field")
    }


def _fixture(base: Path) -> list[Path]:
    customer = _write(base / "SampleCustomer.Table.al", (
        'table 50100 "Sample Customer"\n'
        "{\n"
        "    fields\n"
        "    {\n"
        '        field(1; "No."; Code[20]) { }\n'
        "        field(2; Name; Text[100]) { }\n"
        "    }\n"
        "}\n"
    ))
    ledger = _write(base / "SampleLedgerEntry.Table.al", (
        'table 50101 "Sample Ledger Entry"\n'
        "{\n"
        "    fields\n"
        "    {\n"
        '        field(1; "Entry No."; Integer) { }\n'
        '        field(2; "Customer No."; Code[20]) { }\n'
        "        field(3; Amount; Decimal) { }\n"
        "    }\n"
        "}\n"
    ))
    # Query: two nested dataitems joined by DataItemLink; columns read source
    # fields off each dataitem's bound table.
    query = _write(base / "SampleCustLedger.Query.al", (
        'query 50100 "Sample Cust Ledger"\n'
        "{\n"
        "    elements\n"
        "    {\n"
        "        dataitem(Cust; \"Sample Customer\")\n"
        "        {\n"
        '            column(CustNo; "No.") { }\n'
        "            dataitem(Entry; \"Sample Ledger Entry\")\n"
        "            {\n"
        '                DataItemLink = "Customer No." = Cust."No.";\n'
        "                column(Amt; Amount) { }\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    ))
    # Xmlport: fieldelements read fields off the bound tableelement.
    xmlport = _write(base / "SampleExport.XmlPort.al", (
        'xmlport 50100 "Sample Export"\n'
        "{\n"
        "    schema\n"
        "    {\n"
        "        textelement(Root)\n"
        "        {\n"
        "            tableelement(Cust; \"Sample Customer\")\n"
        "            {\n"
        '                fieldelement(FNo; Cust."No.") { }\n'
        "                fieldelement(FName; Cust.Name) { }\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n"
    ))
    return [customer, ledger, query, xmlport]


def test_query_column_field_lineage(tmp_path: Path):
    result = extract(_fixture(tmp_path / "src"), cache_root=tmp_path / "cache")
    lineage = _lineage(result)
    # Column CustNo reads Sample Customer."No."; Amt reads Sample Ledger Entry.Amount.
    assert (".CustNo", '."No."') in lineage
    assert (".Amt", ".Amount") in lineage


def test_xmlport_fieldelement_field_lineage(tmp_path: Path):
    result = extract(_fixture(tmp_path / "src"), cache_root=tmp_path / "cache")
    lineage = _lineage(result)
    # Fieldelements resolve their <tableelement-var>.<Field> source to the field node.
    assert (".FNo", '."No."') in lineage
    assert (".FName", ".Name") in lineage


def test_field_lineage_is_extracted_not_guessed(tmp_path: Path):
    result = extract(_fixture(tmp_path / "src"), cache_root=tmp_path / "cache")
    edges = _edges(result, "sources_field")
    assert edges, "expected sources_field lineage edges"
    for e in edges:
        assert e["confidence"] == "EXTRACTED"


def test_query_dataitemlink_join_edge(tmp_path: Path):
    result = extract(_fixture(tmp_path / "src"), cache_root=tmp_path / "cache")
    joins = {
        (_label(result, e["source"]), _label(result, e["target"]))
        for e in _edges(result, "joins")
    }
    # The nested Entry dataitem joins to its parent Cust dataitem via DataItemLink.
    assert (".Entry", ".Cust") in joins
    for e in _edges(result, "joins"):
        assert e["confidence"] == "EXTRACTED"


def test_field_lineage_external_source_table_stub(tmp_path: Path):
    # A query column whose dataitem binds a table outside the corpus still emits a
    # lineage edge, landing on an external `Table.Field` stub rather than dropping.
    q = _write(tmp_path / "src" / "OrphanQuery.Query.al", (
        'query 50110 "Sample Orphan"\n'
        "{\n"
        "    elements\n"
        "    {\n"
        "        dataitem(Item; \"Not In Corpus Table\")\n"
        "        {\n"
        '            column(Descr; Description) { }\n'
        "        }\n"
        "    }\n"
        "}\n"
    ))
    result = extract([q], cache_root=tmp_path / "cache")
    targets = {_label(result, e["target"]) for e in _edges(result, "sources_field")}
    assert "Not In Corpus Table.Description" in targets


def test_field_lineage_and_joins_survive_build(tmp_path: Path):
    result = extract(_fixture(tmp_path / "src"), cache_root=tmp_path / "cache")
    g = build_from_json(result)
    rels = [d.get("relation") for _, _, d in g.edges(data=True)]
    assert rels.count("sources_field") >= 4
    assert rels.count("joins") >= 1
