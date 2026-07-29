"""Ordnet Xliff-Einheiten ueber den AL-FNV-Hash der trans-unit-id den Graphknoten zu.
Ergebnis: translations.json mit caption_de / tooltip_de (+ Zustand) und messages_de.
"""
import re, io, json, sys, collections
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from alfnv import al_fnv

PROP = {al_fnv("Caption"): "caption", al_fnv("ToolTip"): "tooltip"}

root = sys.argv[1]
xlf = [p for p in __import__("glob").glob(root + "/app/Translations/*.de-DE.xlf")][0]
x = io.open(xlf, encoding="utf-8").read()

def unesc(s):
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        s = s.replace(a, b)
    return s

# id-Form: "<Type> <hash>[ - <MemberType> <hash>]* [- Property <Prop>]"
TOK = re.compile(r"(?P<type>[A-Za-z]+)\s+(?P<hash>\d+)")
units = []
for u in re.findall(r"<trans-unit[^>]*\bid=\"(.*?)\"[^>]*>(.*?)</trans-unit>", x, re.S):
    uid, body = u
    s = re.search(r"<source>(.*?)</source>", body, re.S)
    t = re.search(r"<target[^>]*>(.*?)</target>", body, re.S)
    st = re.search(r'<target[^>]*state="([^"]*)"', body)
    if not s:
        continue
    toks = TOK.findall(re.sub(r"\s*-\s*Property\s+\d+\s*$", "", uid))
    prop = re.search(r"-\s*Property\s+(\d+)", uid)
    units.append({"src": unesc(s.group(1)), "tgt": unesc(t.group(1)) if t else None,
                  "state": st.group(1) if st else None, "toks": toks,
                  "prop": (int(prop.group(1)) if prop else None), "id": uid})

g = json.load(io.open(root + "/graphify-out/graph.json", encoding="utf-8"))
idx = {n["id"]: n for n in g["nodes"]}
owner = {}
for l in g["links"]:
    if l.get("relation") in ("contains", "method", "trigger") and l["target"] in idx:
        owner.setdefault(l["target"], l["source"])

def obj_node(nid):
    """Das AL-Objekt zu einem Knoten. Ist der direkte Vorfahre ein Dateiknoten,
    dann IST der Knoten selbst das Objekt (Objekt-Beschriftungen wurden sonst nie
    zugeordnet)."""
    par = owner.get(nid)
    if par is None or str(idx.get(par, {}).get("label", "")).endswith(".al"):
        return idx.get(nid, {})
    seen = set()
    while nid in owner and nid not in seen:
        seen.add(nid); nid = owner[nid]
        if not str(idx.get(nid, {}).get("label", "")).endswith(".al"):
            return idx[nid]
    return {}

def clean(lbl):
    lbl = str(lbl or "").strip()
    if lbl.startswith("."): lbl = lbl[1:]
    if lbl.endswith("()"): lbl = lbl[:-2]
    return lbl.strip().strip('"')

# Nachschlagetabellen nach Hash-Kette
by_prop = {}                              # (objhash, memberhash|None, prop) -> unit
msgs = collections.defaultdict(list)      # (objhash, methodhash) -> [(src,tgt,state)]
for u in units:
    hs = [int(h) for _, h in u["toks"]]
    types = [tt.lower() for tt, _ in u["toks"]]
    if not hs: continue
    oh = hs[0]
    if "namedtype" in types:
        mh = hs[1] if len(hs) > 2 else None      # Label in einer Methode
        msgs[(oh, mh)].append((u["src"], u["tgt"], u["state"]))
        continue
    pname = PROP.get(u["prop"])
    if pname:
        mh = hs[1] if len(hs) > 1 else None
        by_prop[(oh, mh, pname)] = u

out, stats = {}, collections.Counter()
for n in g["nodes"]:
    if str(n.get("label", "")).endswith(".al"):
        continue                      # Dateiknoten tragen die Caption doppelt
    o = obj_node(n["id"])
    oname = clean(o.get("label"))
    if not oname: continue
    oh = al_fnv(oname)
    is_obj = (o.get("id") == n.get("id"))
    mh = None if is_obj else al_fnv(clean(n.get("label")))
    rec = {}
    for attr in ("caption", "tooltip"):
        u = by_prop.get((oh, mh, attr))
        if u and u["tgt"]:
            rec[attr + "_de"] = u["tgt"]
            rec[attr + "_de_state"] = u["state"] or "translated"
            stats[attr + "_ok"] += 1
        elif n.get(attr):
            stats[attr + "_miss"] += 1
    m = msgs.get((oh, mh))
    if m:
        rec["messages_de"] = [{"en": a, "de": b, "state": c} for a, b, c in m]
        stats["messages"] += len(m)
    if rec: out[n["id"]] = rec

io.open(root + "/translations.json", "w", encoding="utf-8").write(json.dumps(out, indent=1, ensure_ascii=False))
print("Knoten:", len(out), "|", dict(stats))
