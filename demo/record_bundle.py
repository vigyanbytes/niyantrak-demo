#!/usr/bin/env python3
"""Build the panel bundle for "How we built Niyantrak" from demo/build-record (written by tools/build-record.py).

    python3 demo/record_bundle.py --src <niyantrak checkout> --data demo/build-record --out bundle.json

Same shape as the free edition's tools/demo-data.py bundle, computed the same way: the events go through the real 6W
store (every event validated again) and the decision log through the real decision store (privacy-checked). The fleet
is the two generic machines the record names, with no addresses.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime, time, timezone

FLEET = [("build machine A", "build machine (coding agents)", "linux"),
         ("Windows test laptop", "test laptop", "windows 11")]


def node_id(host):
    return "nyk-" + hashlib.sha256(("build-record:" + host).encode()).hexdigest()[:12]


def card(host, role, os_):
    return {"node_id": node_id(host), "hostname": host, "role": role, "os": os_, "ipv4": [], "ipv6": []}


def build(src, data, out):
    sys.path.insert(0, os.path.abspath(src))
    from niyantrak.decisions import graph
    from niyantrak.decisions.store import Store as DecisionStore
    from niyantrak.telemetry.store import Store
    with open(os.path.join(data, "events.json"), encoding="utf-8") as f:
        ev = json.load(f)
    with open(os.path.join(data, "decisions.jsonl"), encoding="utf-8") as f:
        dec = [json.loads(line) for line in f if line.strip()]
    with open(os.path.join(data, "labels.json"), encoding="utf-8") as f:
        meta = json.load(f)["meta"]
    end = date.fromisoformat(meta["window"][1])
    days = (end - date.fromisoformat(meta["window"][0])).days + 1
    with tempfile.TemporaryDirectory() as tmp:
        st = Store(os.path.join(tmp, "telemetry"), writer="build-record")
        st.append(ev)
        ds = DecisionStore(os.path.join(tmp, "decisions"))
        ds.append(dec, writer="build-record", env={})
        now = datetime.combine(end, time(23, 0), timezone.utc)
        node = {**card(*FLEET[0]), "bootstrapped": True, "checks": []}
        peers = {}
        for f in FLEET[1:]:
            p = card(*f)
            peers[p["node_id"]] = {**p, "verified": True, "seen_at": "during the build"}
        bundle = {"label": meta["label"], "end": str(end), "days": days, "events": len(ev),
                  "activity": st.activity(days=days, now=now), "decisions": graph.export_graph(graph.derive(ds.events())),
                  "node": node, "peers": peers, "joins": {}, "counts": {}}
        filters = {(n["col"], n["value"]) for n in bundle["activity"]["sankey"]["nodes"] if n["value"] is not None}
        filters |= {("why", w["name"]) for w in bundle["activity"]["why"]}
        bundle["activity_by"] = {f"{k}={v}": st.activity(days=days, now=now, filter=(k, v)) for k, v in sorted(filters)}
    for a in [bundle["activity"], *bundle["activity_by"].values()]:
        a["sample"] = meta["label"]
    with open(out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=1, sort_keys=True, default=str)
        f.write("\n")
    return bundle


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    b = build(a.src, a.data, a.out)
    print(f"{b['label']}: {b['events']} 6W events, {len(b['decisions']['nodes'])} decision-log items -> {a.out}")
