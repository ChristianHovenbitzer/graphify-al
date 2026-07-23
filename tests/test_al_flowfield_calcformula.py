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


def _computes_targets(result: dict) -> set[str]:
    """Return the set of target labels reached by computes_from edges.

    Object-level AL facts (extends/subscribes/computes_from) source from the
    owning file node -- the object declaration shares line 1 with it -- so the
    load-bearing assertion is on the resolved source *table* target.
    """
    return {
        _label(result, e["target"])
        for e in result["edges"]
        if e.get("relation") == "computes_from"
    }


def _fixture(base: Path) -> list[Path]:
    # Source table the FlowField sums over (in-corpus).
    src_tbl = _write(base / "SampleLedgerEntry.Table.al", (
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
    # Table carrying the FlowField whose CalcFormula sums the source table.
    host_tbl = _write(base / "SampleCustomer.Table.al", (
        'table 50100 "Sample Customer"\n'
        "{\n"
        "    fields\n"
        "    {\n"
        '        field(1; "No."; Code[20]) { }\n'
        "        field(50; Balance; Decimal)\n"
        "        {\n"
        "            FieldClass = FlowField;\n"
        '            CalcFormula = sum("Sample Ledger Entry".Amount '
        'where("Customer No." = field("No.")));\n'
        "        }\n"
        "        field(51; EntryCount; Integer)\n"
        "        {\n"
        "            FieldClass = FlowField;\n"
        '            CalcFormula = count("Not In Corpus Entry" '
        'where("Customer No." = field("No.")));\n'
        "        }\n"
        "    }\n"
        "}\n"
    ))
    return [src_tbl, host_tbl]


def test_flowfield_calcformula_edge_to_source_table(tmp_path: Path):
    files = _fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    targets = _computes_targets(result)
    # In-corpus source table resolves to its real object node (quoted-object labels
    # keep their quotes, per graphify's AL node-label convention).
    assert '"Sample Ledger Entry"' in targets
    # Source table outside the analyzed corpus becomes an external stub, not dropped.
    assert "Not In Corpus Entry" in targets


def test_flowfield_calcformula_records_source_field(tmp_path: Path):
    files = _fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    members = {
        e.get("member")
        for e in result["edges"]
        if e.get("relation") == "computes_from"
        and _label(result, e["target"]) == '"Sample Ledger Entry"'
    }
    # The `sum(...Amount...)` source field is captured on the edge.
    assert "Amount" in members

    # computes_from edges are structural, not guessed.
    for e in result["edges"]:
        if e.get("relation") == "computes_from":
            assert e["confidence"] == "EXTRACTED"


def test_flowfield_calcformula_edges_survive_build(tmp_path: Path):
    files = _fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    g = build_from_json(result)
    surviving = sum(
        1 for _, _, d in g.edges(data=True)
        if d.get("relation") == "computes_from"
    )
    assert surviving >= 2
