#!/usr/bin/env python3
"""Seed the demo: fictional sample team -> one read-only demo page.

    python3 demo/seed.py --src <niyantrak checkout> --out site [--end YYYY-MM-DD] [--seed 7]

1. Runs the free edition's tools/demo-data.py: a made-up team (asha, ravi, meera, tomas), their AI agents and six
   machines with documentation-range addresses, written through the real 6W store and decision log.
2. Runs tools/build-demo.py --bundle: the panel's own page with that sample data built in.
3. Adapts it for the hosted demo: the "Try it" box calls this server's /api/scan (Niyantrak's Python recognizers),
   the header says "Public demo", and every inline script stays hash-able for the Content-Security-Policy.

Needs the free edition's [telemetry] extra (pyarrow + duckdb) at build time only; the server needs neither.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

# Replaces the in-page scan with the server's: same placeholders, but from the Python recognizers the product ships.
SERVER_SCAN = """(function () {
  const local = window.niyantrakApi;
  window.niyantrakApi = async function (path, body) {
    if (path !== "scan") return local(path, body);
    const r = await fetch("api/scan", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: String((body && body.text) || "") }) });
    let d = {};
    try { d = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
    return d;
  };
})();"""

EDITS = [   # (what the free edition's page says, what the hosted demo says)
    ('<span class="pill info" id="gate">Demo · runs in your browser</span>',
     '<span class="pill info" id="gate">Public demo · read-only · fictional data</span>'),
    ("<title>Niyantrak Panel — demo</title>", "<title>Niyantrak — live demo</title>"),
]


def run(cmd, cwd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def seed(src, out, end, seed_no):
    src = os.path.abspath(src)
    for need in ("tools/demo-data.py", "tools/build-demo.py", "niyantrak/web/standalone-shim.js"):
        if not os.path.exists(os.path.join(src, need)):
            sys.exit(f"{src} is not a niyantrak checkout (missing {need})")
    with tempfile.TemporaryDirectory() as tmp:
        data = os.path.join(tmp, "demo")
        page = os.path.join(tmp, "index.html")
        run([sys.executable, "tools/demo-data.py", "--out", data, "--end", end, "--seed", str(seed_no)], src)
        run([sys.executable, "tools/build-demo.py", page, "--bundle", os.path.join(data, "bundle.json")], src)
        html = open(page, encoding="utf-8").read()
    for old, new in EDITS:
        if old not in html:
            sys.exit(f"build-demo output changed; cannot find: {old}")
        html = html.replace(old, new)
    assert "</script" not in SERVER_SCAN
    html = html.replace("</body>", f"<script>\n{SERVER_SCAN}\n</script>\n</body>", 1)
    if "Sample data" not in html:
        sys.exit("demo page is missing the 'Sample data' label")
    os.makedirs(out, exist_ok=True)
    target = os.path.join(out, "index.html")
    with open(target, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {target} ({len(html) // 1024} KB, sample data ending {end})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, help="a checkout of github.com/vigyanbytes/niyantrak")
    ap.add_argument("--out", default="site")
    ap.add_argument("--end", default="", help="last day of sample activity (default: today, UTC)")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    seed(a.src, a.out, a.end or str(datetime.now(timezone.utc).date()), a.seed)
