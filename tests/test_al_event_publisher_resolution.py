from __future__ import annotations

from pathlib import Path

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


def _node_by_label(result: dict, label: str) -> dict | None:
    for n in result["nodes"]:
        if n.get("label") == label:
            return n
    return None


def _al_fixture(base: Path) -> list[Path]:
    pub = _write(base / "EventPub.Codeunit.al", (
        "codeunit 50000 EventPub\n"
        "{\n"
        "    [IntegrationEvent(false, false)]\n"
        "    local procedure OnBeforeDoThing(var Rec: Record Customer)\n"
        "    begin\n"
        "    end;\n"
        "\n"
        "    [BusinessEvent(false)]\n"
        "    local procedure OnAfterDoThing(var Rec: Record Customer)\n"
        "    begin\n"
        "    end;\n"
        "\n"
        "    procedure PlainProc()\n"
        "    begin\n"
        "    end;\n"
        "}\n"
    ))
    sub = _write(base / "EventSub.Codeunit.al", (
        "codeunit 50001 EventSub\n"
        "{\n"
        "    [EventSubscriber(ObjectType::Codeunit, Codeunit::EventPub, 'OnBeforeDoThing', '', false, false)]\n"
        "    local procedure HandleBefore(var Rec: Record Customer)\n"
        "    begin\n"
        "    end;\n"
        "}\n"
    ))
    return [pub, sub]


def test_event_publisher_procs_carry_event_attribute(tmp_path: Path):
    files = _al_fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    before = _node_by_label(result, ".OnBeforeDoThing()")
    after = _node_by_label(result, ".OnAfterDoThing()")
    plain = _node_by_label(result, ".PlainProc()")

    assert before is not None and before.get("event") == "integration"
    assert after is not None and after.get("event") == "business"
    # A plain procedure carries no event marker.
    assert plain is not None and plain.get("event") is None


def test_subscribes_edge_resolves_to_publisher_procedure(tmp_path: Path):
    files = _al_fixture(tmp_path / "src")
    result = extract(files, cache_root=tmp_path / "cache")

    subs = [e for e in result["edges"] if e.get("relation") == "subscribes"]
    assert len(subs) == 1
    edge = subs[0]

    # The edge originates at the subscriber procedure ...
    assert _label(result, edge["source"]) == ".HandleBefore()"
    # ... and targets the publisher PROCEDURE node, not the publisher OBJECT.
    assert _label(result, edge["target"]) == ".OnBeforeDoThing()"

    pub_obj = _node_by_label(result, "EventPub")
    assert pub_obj is not None
    assert edge["target"] != pub_obj["id"]
