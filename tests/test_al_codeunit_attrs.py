from __future__ import annotations

from pathlib import Path

from graphify.extract import extract_al


# Synthetic codeunit exercising every #39 attribute: object properties, the
# three procedure scopes, an OnRun + install trigger, and [TryFunction].
_CODEUNIT = """\
codeunit 50100 "Sample Install Mgt"
{
    SingleInstance = true;
    Subtype = Install;
    Access = Internal;
    Permissions = tabledata Customer = RIMD;
    InherentEntitlements = X;
    InherentPermissions = X;

    trigger OnRun()
    begin
    end;

    trigger OnInstallAppPerCompany()
    begin
    end;

    [TryFunction]
    local procedure TryParse(Input: Text): Boolean
    begin
    end;

    internal procedure DoInternal()
    begin
    end;

    procedure DoPublic()
    begin
    end;
}
"""

# A plain upgrade codeunit + a normal helper, to check trigger-kind typing and
# that a scope-only procedure carries no stray TryFunction flag.
_UPGRADE = """\
codeunit 50101 "Sample Upgrade Mgt"
{
    Subtype = Upgrade;

    trigger OnUpgradePerCompany()
    begin
    end;

    procedure Helper()
    begin
    end;
}
"""


def _node(nodes: list[dict], label: str) -> dict:
    return next(n for n in nodes if n.get("label") == label)


def _obj(nodes: list[dict]) -> dict:
    # The object node is the non-file node on the declaration line (label is the
    # object name, not the ".al" file node and not a ".member").
    for n in nodes:
        lbl = str(n.get("label", ""))
        if not lbl.endswith(".al") and not lbl.startswith("."):
            return n
    raise AssertionError("no object node")


def test_al_object_properties_on_object_node(tmp_path: Path):
    f = tmp_path / "SampleInstallMgt.Codeunit.al"
    f.write_text(_CODEUNIT, encoding="utf-8")

    nodes = extract_al(f)["nodes"]
    obj = _obj(nodes)

    assert obj["al_single_instance"] == "true"
    assert obj["al_subtype"] == "Install"
    assert obj["al_access"] == "Internal"
    assert obj["al_permissions"] == "tabledata Customer = RIMD"
    assert obj["al_inherent_entitlements"] == "X"
    assert obj["al_inherent_permissions"] == "X"

    # The file node must NOT inherit the object's properties (no bleed-through),
    # even though it shares the declaration line.
    file_node = next(n for n in nodes if str(n.get("label", "")).endswith(".al"))
    assert "al_subtype" not in file_node
    assert "al_single_instance" not in file_node


def test_al_procedure_scope_and_try_function(tmp_path: Path):
    f = tmp_path / "SampleInstallMgt.Codeunit.al"
    f.write_text(_CODEUNIT, encoding="utf-8")

    nodes = extract_al(f)["nodes"]

    tryp = _node(nodes, ".TryParse()")
    assert tryp["al_scope"] == "local"
    assert tryp["al_try_function"] is True

    internal = _node(nodes, ".DoInternal()")
    assert internal["al_scope"] == "internal"
    assert "al_try_function" not in internal

    public = _node(nodes, ".DoPublic()")
    assert public["al_scope"] == "global"
    assert "al_try_function" not in public


def test_al_trigger_typing(tmp_path: Path):
    f = tmp_path / "SampleInstallMgt.Codeunit.al"
    f.write_text(_CODEUNIT, encoding="utf-8")

    nodes = extract_al(f)["nodes"]

    onrun = _node(nodes, ".OnRun()")
    assert onrun["al_trigger"] == "OnRun"
    assert onrun["al_trigger_kind"] == "run"

    install = _node(nodes, ".OnInstallAppPerCompany()")
    assert install["al_trigger"] == "OnInstallAppPerCompany"
    assert install["al_trigger_kind"] == "install"


def test_al_upgrade_trigger_and_clean_helper(tmp_path: Path):
    f = tmp_path / "SampleUpgradeMgt.Codeunit.al"
    f.write_text(_UPGRADE, encoding="utf-8")

    nodes = extract_al(f)["nodes"]

    assert _obj(nodes)["al_subtype"] == "Upgrade"

    upg = _node(nodes, ".OnUpgradePerCompany()")
    assert upg["al_trigger_kind"] == "upgrade"

    helper = _node(nodes, ".Helper()")
    assert helper["al_scope"] == "global"
    # Triggers are not procedures: no scope key on trigger nodes, no trigger key
    # on procedure nodes.
    assert "al_trigger" not in helper
    assert "al_scope" not in upg


def test_al_semantic_attrs_are_additive(tmp_path: Path):
    f = tmp_path / "SampleInstallMgt.Codeunit.al"
    f.write_text(_CODEUNIT, encoding="utf-8")

    nodes = extract_al(f)["nodes"]
    for n in nodes:
        assert n.get("id")
        # No empty/malformed semantic keys are ever added.
        if "al_scope" in n:
            assert n["al_scope"] in ("local", "internal", "global")
        if "al_trigger_kind" in n:
            assert n["al_trigger_kind"] in ("run", "install", "upgrade", "other")
