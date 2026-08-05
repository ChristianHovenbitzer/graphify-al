"""Control add-in event declarations + page `usercontrol` usage and calls (#41).

Two gaps this covers:

1. A controladdin's `event(...)` declarations used to be dropped: `event_declaration`
   was not in the AL function types, so the JS->AL event contract was invisible.
   They are now emitted as `.OnFoo()` nodes parented to the add-in (a `method` edge),
   exactly like the add-in's procedures.

2. A page `usercontrol(<ctrl>; <AddIn>)` produced no page->add-in edge, and
   `CurrPage.<ctrl>.<proc>()` calls were not resolved by the typed AL layer (so an
   add-in looked called-by-nobody across files). `_al_collect_facts` now emits a
   `usercontrol` fact (page object -> add-in) and, using the page's control->add-in
   map, resolves `CurrPage.<ctrl>.<proc>()` to a cross-file `calls` edge landing on
   the add-in's procedure node.
"""

from graphify.extract import extract


def _node_by_label(nodes, label):
    # Real AL object nodes now carry the bare name separately in
    # `al_object_name` (their `label` is the full type+ID+name canonical
    # reference, spec 004-al-object-labels) -- prefer that, falling back to
    # the old strip('"') match for non-object nodes (e.g. member/procedure
    # nodes, which are unaffected by that change).
    return next(
        n for n in nodes
        if n.get("al_object_name") == label
        or str(n.get("label", "")).strip('"') == label
    )


def _addin_and_page(tmp_path):
    (tmp_path / "addin.al").write_text(
        "namespace Demo.App;\n"
        "controladdin PdfViewer\n"
        "{\n"
        "    event OnReady();\n"
        "    event OnDocumentLoaded(name: Text; pages: Integer);\n"
        "    procedure LoadDocument(url: Text);\n"
        "}\n",
        encoding="utf-8",
    )
    (tmp_path / "page.al").write_text(
        "namespace Demo.App;\n"
        'page 50100 "Viewer Card"\n'
        "{\n"
        "    layout\n"
        "    {\n"
        "        area(content)\n"
        "        {\n"
        "            usercontrol(Viewer; PdfViewer)\n"
        "            {\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    procedure Open()\n"
        "    begin\n"
        "        CurrPage.Viewer.LoadDocument('doc://a');\n"
        "    end;\n"
        "}\n",
        encoding="utf-8",
    )
    return extract([tmp_path / "addin.al", tmp_path / "page.al"], cache_root=tmp_path)


def test_controladdin_event_declarations_become_nodes(tmp_path):
    result = _addin_and_page(tmp_path)
    addin = _node_by_label(result["nodes"], "PdfViewer")

    # Each `event(...)` is a node parented to the add-in via a `method` edge.
    method_targets = {
        e["target"] for e in result["edges"]
        if e["relation"] == "method" and e["source"] == addin["id"]
    }
    by_id = {n["id"]: n for n in result["nodes"]}
    labels = {by_id[t]["label"] for t in method_targets if t in by_id}
    assert ".OnReady()" in labels, labels
    assert ".OnDocumentLoaded()" in labels, labels
    # The add-in's procedure is still captured too (unchanged behaviour).
    assert ".LoadDocument()" in labels, labels


def test_page_usercontrol_emits_page_to_addin_edge(tmp_path):
    result = _addin_and_page(tmp_path)
    page = _node_by_label(result["nodes"], "Viewer Card")
    addin = _node_by_label(result["nodes"], "PdfViewer")

    edge = next(
        (e for e in result["edges"]
         if e["relation"] == "usercontrol"
         and e["source"] == page["id"] and e["target"] == addin["id"]),
        None,
    )
    assert edge is not None, [e for e in result["edges"] if e["relation"] == "usercontrol"]
    assert edge["confidence"] == "EXTRACTED"
    assert edge["context"] == "al_usercontrol"


def test_currpage_control_call_resolves_to_addin_procedure(tmp_path):
    result = _addin_and_page(tmp_path)
    addin = _node_by_label(result["nodes"], "PdfViewer")

    # `.Open()` on the page -> `.LoadDocument()` on the add-in, resolved through the
    # usercontrol map (cross-file; the two objects live in different files).
    open_proc = next(
        n for n in result["nodes"]
        if n.get("label") == ".Open()" and "viewer" in n["id"].split("_open")[0]
    )
    load_proc = next(
        n for n in result["nodes"]
        if n.get("label") == ".LoadDocument()" and n["id"].startswith(addin["id"] + "_")
    )
    calls = [
        e for e in result["edges"]
        if e["relation"] == "calls"
        and e["source"] == open_proc["id"] and e["target"] == load_proc["id"]
    ]
    assert calls, [e for e in result["edges"] if e["relation"] == "calls"]
