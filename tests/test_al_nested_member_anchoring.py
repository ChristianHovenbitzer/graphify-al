"""Nested-member trigger anchoring, `_make_id` blank-name guard, member captions.

Generalizes the #26 field-OnValidate fix to ALL nested member triggers across
every AL object type (#34), guards `_make_id` against member names that
normalize to empty (#35), and attaches Caption/ToolTip to the member's own node
instead of bleeding onto the object (#36).

A trigger nested inside an object member reaches the generic recurse with the
parent object cleared; before the fix it fell to a file-level fallback keyed only
by trigger type, so every same-type trigger in a file collapsed onto one node
(all but one dropped) and the survivor absorbed the others' `calls` edges. Each
nested trigger must instead get an object+member-qualified id and hang off its
member node, and two triggers' call edges must stay on their own trigger nodes.

Fixtures are fully synthetic: standard BC table names (Customer, Item) and
invented 50xxx objects only.
"""
import pytest

pytest.importorskip("tree_sitter_al", reason="tree-sitter-al not installed")

from graphify.extract import extract_al  # noqa: E402


def _trigger_edges(result):
    return {e["source"]: e["target"]
            for e in result["edges"] if e["relation"] == "trigger"}


def _calls_from(result, src):
    return {e["target"] for e in result["edges"]
            if e["relation"] == "calls" and e["source"] == src}


def _member(result, label):
    return next(n for n in result["nodes"] if n.get("label") == label)


def _labels(result):
    out: dict[str, list] = {}
    for n in result["nodes"]:
        out.setdefault(n["label"], []).append(n)
    return out


# --- #34: page control + action triggers --------------------------------------

_PAGE = """page 50100 "Widget Card"
{
    layout
    {
        area(content)
        {
            field(Name; Rec.Name)
            {
                trigger OnValidate() begin HandleName(); end;
            }
            field(City; Rec.City)
            {
                trigger OnValidate() begin HandleCity(); end;
            }
        }
    }
    actions
    {
        area(processing)
        {
            action(Post)
            {
                trigger OnAction() begin DoPost(); end;
            }
        }
    }
    procedure HandleName() begin end;
    procedure HandleCity() begin end;
    procedure DoPost() begin end;
}
"""


def test_page_control_and_action_triggers_are_member_parented_no_bleed(tmp_path):
    p = tmp_path / "WidgetCard.al"
    p.write_text(_PAGE, encoding="utf-8")
    result = extract_al(p)

    name_ctrl = _member(result, ".Name")
    city_ctrl = _member(result, ".City")
    post_act = _member(result, ".Post")
    trig = _trigger_edges(result)

    # Each control/action owns its own trigger node.
    assert name_ctrl["id"] in trig
    assert city_ctrl["id"] in trig
    assert post_act["id"] in trig

    t_name, t_city, t_post = (trig[name_ctrl["id"]], trig[city_ctrl["id"]],
                              trig[post_act["id"]])
    # Two same-type (OnValidate) triggers are DISTINCT nodes, member-qualified.
    assert t_name != t_city
    assert t_name.startswith(name_ctrl["id"] + "_")
    assert t_city.startswith(city_ctrl["id"] + "_")
    assert t_post.startswith(post_act["id"] + "_")

    # No collapsed file/object-level trigger node survives.
    labels = _labels(result)
    assert "OnValidate()" not in labels
    assert "OnAction()" not in labels

    # Call-edge provenance stays on the correct trigger (no bleed-through).
    assert _member(result, ".HandleName()")["id"] in _calls_from(result, t_name)
    assert _member(result, ".HandleCity()")["id"] not in _calls_from(result, t_name)
    assert _member(result, ".HandleCity()")["id"] in _calls_from(result, t_city)
    assert _member(result, ".DoPost()")["id"] in _calls_from(result, t_post)


# --- #34: report dataitem triggers --------------------------------------------

_REPORT = """report 50101 "Widget Rep"
{
    dataset
    {
        dataitem(Cust; Customer)
        {
            trigger OnAfterGetRecord() begin RepA(); end;
        }
        dataitem(ItemLine; Item)
        {
            trigger OnAfterGetRecord() begin RepB(); end;
        }
    }
    procedure RepA() begin end;
    procedure RepB() begin end;
}
"""


def test_report_dataitem_triggers_are_member_parented_no_bleed(tmp_path):
    p = tmp_path / "WidgetRep.al"
    p.write_text(_REPORT, encoding="utf-8")
    result = extract_al(p)

    cust = _member(result, ".Cust")
    item = _member(result, ".ItemLine")
    trig = _trigger_edges(result)

    assert cust["id"] in trig and item["id"] in trig
    t_cust, t_item = trig[cust["id"]], trig[item["id"]]
    assert t_cust != t_item
    assert t_cust.startswith(cust["id"] + "_")
    assert t_item.startswith(item["id"] + "_")

    assert _member(result, ".RepA()")["id"] in _calls_from(result, t_cust)
    assert _member(result, ".RepB()")["id"] not in _calls_from(result, t_cust)
    assert _member(result, ".RepB()")["id"] in _calls_from(result, t_item)


# --- #34: xmlport element triggers (nested member qualification) --------------

_XMLPORT = """xmlport 50102 "Widget XP"
{
    schema
    {
        textelement(Root)
        {
            tableelement(Cust; Customer)
            {
                trigger OnAfterGetRecord() begin XpA(); end;
            }
            tableelement(ItemLine; Item)
            {
                trigger OnAfterGetRecord() begin XpB(); end;
            }
        }
    }
    procedure XpA() begin end;
    procedure XpB() begin end;
}
"""


def test_xmlport_element_triggers_are_member_parented_no_bleed(tmp_path):
    p = tmp_path / "WidgetXp.al"
    p.write_text(_XMLPORT, encoding="utf-8")
    result = extract_al(p)

    cust = _member(result, ".Cust")
    item = _member(result, ".ItemLine")
    trig = _trigger_edges(result)

    assert cust["id"] in trig and item["id"] in trig
    t_cust, t_item = trig[cust["id"]], trig[item["id"]]
    assert t_cust != t_item

    # Nested tableelements are qualified by their enclosing textelement (Root),
    # so the member id carries the full chain, not just the object.
    root = _member(result, ".Root")
    assert cust["id"].startswith(root["id"] + "_")
    assert item["id"].startswith(root["id"] + "_")

    assert _member(result, ".XpA()")["id"] in _calls_from(result, t_cust)
    assert _member(result, ".XpB()")["id"] not in _calls_from(result, t_cust)
    assert _member(result, ".XpB()")["id"] in _calls_from(result, t_item)


# --- #35: blank enum value -> real distinct node, no self-loop ----------------

_ENUM = """enum 50103 "Widget State"
{
    Extensible = true;
    value(0; "") { Caption = ''; }
    value(1; Active) { Caption = 'Active'; }
}
"""


def test_blank_enum_value_gets_distinct_node_no_self_loop(tmp_path):
    p = tmp_path / "WidgetState.al"
    p.write_text(_ENUM, encoding="utf-8")
    result = extract_al(p)

    enum = _member(result, '"Widget State"')
    contains = [(e["source"], e["target"]) for e in result["edges"]
                if e["relation"] == "contains"]

    # No `contains` edge degenerates to a self-loop on the enum object.
    assert (enum["id"], enum["id"]) not in contains

    # The blank value is a real, distinct node contained by the enum, separate
    # from the named `Active` value.
    enum_members = [t for (s, t) in contains if s == enum["id"]]
    assert len(enum_members) == 2
    assert len(set(enum_members)) == 2
    active = _member(result, ".Active")
    assert active["id"] in enum_members
    blank_id = next(m for m in enum_members if m != active["id"])
    assert blank_id != enum["id"]


def test_two_members_normalizing_equal_do_not_merge(tmp_path):
    # `"No."` and `No` normalize to the same id segment; guard keeps them distinct.
    src = ('table 50104 "Widget Coll"\n{\n    fields\n    {\n'
           '        field(1; "No."; Code[20]) { }\n'
           '        field(2; No; Code[20]) { }\n'
           '    }\n}\n')
    p = tmp_path / "WidgetColl.al"
    p.write_text(src, encoding="utf-8")
    result = extract_al(p)

    table = _member(result, '"Widget Coll"')
    members = [t for (s, t) in
               [(e["source"], e["target"]) for e in result["edges"]
                if e["relation"] == "contains"]
               if s == table["id"]]
    assert len(members) == 2
    assert len(set(members)) == 2  # not merged onto one id


# --- #36: tableextension object node does not inherit added field's caption ---

_TABLEEXT = """tableextension 50105 "Widget Ext" extends Customer
{
    fields
    {
        field(50100; "Widget Code"; Code[20])
        {
            Caption = 'Widget Code';
            ToolTip = 'Specifies the widget code.';
        }
    }
}
"""


def test_tableextension_object_does_not_inherit_field_caption(tmp_path):
    p = tmp_path / "WidgetExt.al"
    p.write_text(_TABLEEXT, encoding="utf-8")
    result = extract_al(p)

    obj = _member(result, '"Widget Ext"')
    field = _member(result, '."Widget Code"')

    # Object node (which has no own Caption) must NOT adopt the field's caption.
    assert "caption" not in obj
    assert "tooltip" not in obj
    # The field node carries its own Caption/ToolTip.
    assert field["caption"] == "Widget Code"
    assert field["tooltip"] == "Specifies the widget code."
