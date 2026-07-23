"""AL report/query dataset dataitem + column -> source-field extraction (#32).

Reports/queries previously exposed only their object node; the dataset's
dataitem/column structure and each column's source-field mapping were opaque.
These tests verify that:

- a report dataitem is a member node `contains`-ed by its report object,
- each dataset column is a member node `contains`-ed by its dataitem,
- a column whose source is a bare field reference emits a `references` edge
  (context `al_column_source`) to that field's node on the dataitem's bound
  table — a real in-corpus field node when present, an external stub otherwise,
- a column whose source is a computed expression emits no such edge,
- the same holds for query dataitems/columns.

Fixtures are fully synthetic: standard BC table names (Customer) and invented
50xxx objects only.
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract, extract_al  # noqa: E402


def _contains(result, src_id):
    return {e["target"] for e in result["edges"]
            if e["relation"] == "contains" and e["source"] == src_id}


def _member(result, label):
    return next(n for n in result["nodes"] if n.get("label") == label)


def _col_source_edges(result):
    return [e for e in result["edges"] if e.get("context") == "al_column_source"]


_REPORT = '''report 50000 "Cust List"
{
    dataset
    {
        dataitem(Cust; Customer)
        {
            column(CustName; Name) { }
            column(Bal; "Balance (LCY)") { }
            column(Doubled; Name + Name) { }
        }
    }
}
'''

_CUSTOMER = '''table 18 Customer
{
    fields
    {
        field(2; Name; Text[100]) { }
        field(59; "Balance (LCY)"; Decimal) { }
    }
}
'''


def test_report_dataitem_and_columns_are_member_nodes(tmp_path):
    p = tmp_path / "CustList.Report.al"
    p.write_text(_REPORT, encoding="utf-8")
    result = extract_al(p)

    report = _member(result, '"Cust List"')
    dataitem = _member(result, ".Cust")

    # The dataitem hangs off the report object; the columns hang off the dataitem.
    assert dataitem["id"] in _contains(result, report["id"])
    di_members = _contains(result, dataitem["id"])
    col_labels = {_member(result, lbl)["id"] for lbl in (".CustName", ".Bal", ".Doubled")}
    assert col_labels <= di_members


def test_column_source_edges_resolve_to_real_field_nodes(tmp_path):
    rep = tmp_path / "CustList.Report.al"
    cust = tmp_path / "Customer.Table.al"
    rep.write_text(_REPORT, encoding="utf-8")
    cust.write_text(_CUSTOMER, encoding="utf-8")

    result = extract([rep, cust], cache_root=tmp_path)

    name_fld = _member(result, ".Name")
    bal_fld = _member(result, '."Balance (LCY)"')
    cust_name_col = _member(result, ".CustName")
    bal_col = _member(result, ".Bal")

    edges = _col_source_edges(result)
    by_src = {e["source"]: e for e in edges}

    # Bare field-reference columns map to their source-field node on Customer.
    assert cust_name_col["id"] in by_src
    assert by_src[cust_name_col["id"]]["target"] == name_fld["id"]
    assert by_src[cust_name_col["id"]]["relation"] == "references"
    assert by_src[cust_name_col["id"]]["confidence"] == "EXTRACTED"

    assert bal_col["id"] in by_src
    assert by_src[bal_col["id"]]["target"] == bal_fld["id"]

    # The computed-expression column (Name + Name) has no single source field.
    doubled_col = _member(result, ".Doubled")
    assert doubled_col["id"] not in by_src


def test_column_source_unresolved_table_becomes_external_stub(tmp_path):
    # Bound table lives outside the corpus -> the source field is an external stub.
    rep = tmp_path / "Lonely.Report.al"
    rep.write_text(
        'report 50001 "Lonely"\n'
        '{\n'
        '    dataset\n'
        '    {\n'
        '        dataitem(Ven; Vendor)\n'
        '        {\n'
        '            column(VenName; Name) { }\n'
        '        }\n'
        '    }\n'
        '}\n',
        encoding="utf-8",
    )

    result = extract([rep], cache_root=tmp_path)

    edges = _col_source_edges(result)
    assert len(edges) == 1
    stub = next(n for n in result["nodes"] if n["id"] == edges[0]["target"])
    assert stub["file_type"] == "external"
    assert stub["label"] == "Vendor.Name"


_QUERY = '''query 50002 "Cust Query"
{
    elements
    {
        dataitem(Cust; Customer)
        {
            column(CName; Name) { }
        }
    }
}
'''


def test_query_dataitem_and_column_source(tmp_path):
    qry = tmp_path / "CustQuery.Query.al"
    cust = tmp_path / "Customer.Table.al"
    qry.write_text(_QUERY, encoding="utf-8")
    cust.write_text(_CUSTOMER, encoding="utf-8")

    result = extract([qry, cust], cache_root=tmp_path)

    query = _member(result, '"Cust Query"')
    dataitem = _member(result, ".Cust")
    col = _member(result, ".CName")
    name_fld = _member(result, ".Name")

    assert dataitem["id"] in _contains(result, query["id"])
    assert col["id"] in _contains(result, dataitem["id"])

    edges = _col_source_edges(result)
    assert len(edges) == 1
    assert edges[0]["source"] == col["id"]
    assert edges[0]["target"] == name_fld["id"]
