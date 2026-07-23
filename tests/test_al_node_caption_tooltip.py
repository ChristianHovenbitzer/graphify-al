from __future__ import annotations

from pathlib import Path

from graphify.extract import extract_al


_SAMPLE = """\
/// <summary>The sample record table.</summary>
table 50100 SampleThing
{
    fields
    {
        field(1; "Entry No."; Integer) { }
        field(2; Name; Text[100])
        {
            Caption = 'Name';
            ToolTip = 'Specifies the name.';
        }
    }
    var
        SampleLbl: Label 'A reusable message.';

    /// <summary>Does the thing.</summary>
    procedure DoThing()
    begin
    end;
}
"""


def _by_line(nodes: list[dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for n in nodes:
        loc = str(n.get("source_location", ""))
        if loc[:1] == "L":
            out[int(loc[1:])] = n
    return out


def test_al_node_text_attributes(tmp_path: Path):
    f = tmp_path / "SampleThing.Table.al"
    f.write_text(_SAMPLE, encoding="utf-8")

    result = extract_al(f)
    nodes = result["nodes"]
    by_line = _by_line(nodes)

    # Object node (table declaration on line 2).
    obj = by_line[2]
    assert obj["label"] == "SampleThing"          # existing display name is untouched
    assert obj["doc"] == "The sample record table."
    assert obj["al_label"] == "A reusable message."
    # Fields have no dedicated graph node, so their Caption/ToolTip attach to the
    # nearest enclosing (object) node.
    assert obj["caption"] == "Name"
    assert obj["tooltip"] == "Specifies the name."

    # Procedure node carries its own /// summary as `doc`.
    proc = next(n for n in nodes if n.get("label") == ".DoThing()")
    assert proc["doc"] == "Does the thing."


def test_al_text_attributes_are_additive(tmp_path: Path):
    f = tmp_path / "SampleThing.Table.al"
    f.write_text(_SAMPLE, encoding="utf-8")

    nodes = extract_al(f)["nodes"]
    # Nodes without any caption/tooltip/doc/label stay clean (no empty keys added),
    # and ids/labels are never rewritten by the attachment pass.
    for n in nodes:
        assert n.get("id")
        for k in ("caption", "tooltip", "doc", "al_label"):
            assert k not in n or isinstance(n[k], str)
