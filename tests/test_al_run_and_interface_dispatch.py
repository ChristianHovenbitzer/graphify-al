from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_al")

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


def _calls_edges(result: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for e in result["edges"]:
        if e.get("relation") == "calls":
            out.add((_label(result, e["source"]), _label(result, e["target"])))
    return out


def test_al_codeunit_run_dispatch_resolves(tmp_path: Path):
    # Indirect dispatch: Codeunit.Run(Codeunit::"X") -> a `calls` edge to X.
    impl = _write(tmp_path / "MyImpl.Codeunit.al", (
        'codeunit 50101 "My Impl"\n'
        "{\n"
        "    procedure DoThing()\n"
        "    begin\n"
        "    end;\n"
        "}\n"
    ))
    caller = _write(tmp_path / "Runner.Codeunit.al", (
        'codeunit 50100 "Runner"\n'
        "{\n"
        "    procedure Go()\n"
        "    begin\n"
        '        Codeunit.Run(Codeunit::"My Impl");\n'
        "    end;\n"
        "}\n"
    ))
    result = extract([impl, caller], cache_root=tmp_path / "cache")

    assert (".Go()", '"My Impl"') in _calls_edges(result)


def test_al_interface_variable_dispatch_fans_out(tmp_path: Path):
    # A call on an `Interface "IPay"`-typed variable fans out to that method on
    # every object that implements IPay (an over-approximation, by design).
    iface = _write(tmp_path / "IPay.al",
                   "interface IPay\n{\n    procedure Charge();\n}\n")
    card = _write(tmp_path / "PayCard.al",
                  "codeunit 50100 PayCard implements IPay\n{\n"
                  "    procedure Charge() begin end;\n}\n")
    paypal = _write(tmp_path / "PayPalImpl.al",
                    "codeunit 50102 PayPalImpl implements IPay\n{\n"
                    "    procedure Charge() begin end;\n}\n")
    caller = _write(tmp_path / "Caller.al", (
        'codeunit 50103 "Caller"\n'
        "{\n"
        "    procedure Pay()\n"
        "    var\n"
        '        Payer: Interface IPay;\n'
        "    begin\n"
        "        Payer.Charge();\n"
        "    end;\n"
        "}\n"
    ))
    result = extract([iface, card, paypal, caller], cache_root=tmp_path / "cache")

    edges = _calls_edges(result)
    assert (".Pay()", ".Charge()") in edges  # fans out to both implementors' Charge


def test_al_interface_variable_dispatch_lands_on_object_when_no_proc(tmp_path: Path):
    # If the implementor has no matching procedure node, the fan-out lands on the
    # implementing object itself rather than dropping the edge.
    iface = _write(tmp_path / "IPay.al",
                   "interface IPay\n{\n    procedure Charge();\n}\n")
    card = _write(tmp_path / "PayCard.al",
                  "codeunit 50100 PayCard implements IPay\n{\n"
                  "    procedure Other() begin end;\n}\n")
    caller = _write(tmp_path / "Caller.al", (
        'codeunit 50103 "Caller"\n'
        "{\n"
        "    procedure Pay()\n"
        "    var\n"
        '        Payer: Interface IPay;\n'
        "    begin\n"
        "        Payer.Charge();\n"
        "    end;\n"
        "}\n"
    ))
    result = extract([iface, card, caller], cache_root=tmp_path / "cache")

    assert (".Pay()", "PayCard") in _calls_edges(result)
