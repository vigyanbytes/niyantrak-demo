#!/usr/bin/env python3
"""Seed the demo: fictional sample team + our own build record -> two read-only demo pages.

    python3 demo/seed.py --src <niyantrak checkout> --out site [--end YYYY-MM-DD] [--seed 7] [--record demo/build-record]

1. Runs the free edition's tools/demo-data.py: a made-up team (asha, ravi, meera, tomas), their AI agents and six
   machines with documentation-range addresses, written through the real 6W store and decision log.
2. Runs tools/build-demo.py --bundle: the panel's own page with that sample data built in.
3. Adapts it for the hosted demo: the "Try it" box calls this server's /api/scan (Niyantrak's Python recognizers),
   the header says "Public demo", and every inline script stays hash-able for the Content-Security-Policy.
4. "How we built Niyantrak" (site/build-record/index.html): the same panel built from demo/build-record — the real,
   sanitised record of the AI agents that built Niyantrak (tools/build-record.py) — through the real 6W store and
   decision log (demo/record_bundle.py). Both pages get a dataset switch and a link to the sign-up page.

Needs the free edition's [telemetry] extra (pyarrow + duckdb) at build time only; the server needs neither.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# Replaces the in-page scan with the server's: same placeholders, but from the Python recognizers the product ships.
SERVER_SCAN = """(function () {
  const local = window.niyantrakApi;
  window.niyantrakApi = async function (path, body) {
    if (path !== "scan") return local(path, body);
    const r = await fetch("/api/scan", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: String((body && body.text) || "") }) });
    let d = {};
    try { d = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
    return d;
  };
})();"""

GATE_PILL = '<span class="pill info" id="gate">Demo · runs in your browser</span>'
EDITS = [   # (what the free edition's page says, what the hosted demo says)
    (GATE_PILL, '<span class="pill info" id="gate">Public demo · read-only · fictional data</span>'),
    ("<title>Niyantrak Panel — demo</title>", "<title>Niyantrak — live demo</title>"),
]
RECORD_EDITS = [
    (GATE_PILL, '<span class="pill info" id="gate">Public demo · read-only · real build record, sanitised</span>'),
    ('<span class="pill warn" id="demo-badge" hidden>Sample data</span>',
     '<span class="pill warn" id="demo-badge" hidden>Real build record</span>'),
    ("<title>Niyantrak Panel — demo</title>", "<title>Niyantrak — how we built it</title>"),
]
SIGNUP = "https://www.vigyanbytes.com/niyantrak/signup"
RECORD_SOURCE = "https://github.com/vigyanbytes/niyantrak-demo/blob/main/tools/build-record.py"
SWITCH_CSS = """<style>
.dset{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;padding:10px 24px;background:var(--card);
  border-bottom:1px solid var(--line);color:var(--body);font-size:13.5px;line-height:19px}
.dset .seg{display:inline-flex;border:1px solid var(--line2);border-radius:8px;overflow:hidden;flex:none}
.dset .seg a{padding:6px 12px;text-decoration:none;color:var(--body);font-weight:600}
.dset .seg a[aria-current=page]{background:var(--primary);color:var(--on)}
.dset .seg a:not([aria-current=page]):hover{background:var(--sunk)}
.dset p{margin:0;flex:1 1 360px;max-width:96ch}
.dset .cta{flex:none;padding:7px 14px;border-radius:8px;background:var(--ink);color:var(--bg);font-weight:650;text-decoration:none}
.dset .cta:hover{opacity:.9}
@media (max-width:720px){.dset{padding:10px 16px}.dset .seg{width:100%}.dset .seg a{flex:1;text-align:center}}
</style>"""
EXPLAIN = {
    "sample": "A <b>fictional sample team</b> (asha, ravi, meera, tomas) and their AI agents. Switch to "
              "<b>How we built Niyantrak</b> for our own real record.",
    "build-record": "These are the real, sanitised records of the AI agents that built Niyantrak: what they did, what "
                    "our gate blocked, where we slipped and disclosed it. 21–25 Oct 2026; roles instead of names, "
                    "generic machines, counts only. The gate log covers all of the agent team's work that week, not "
                    "only Niyantrak, and never records the command, so those events show no project — "
                    "<a href=\"" + RECORD_SOURCE + "\" target=\"_blank\" rel=\"noopener\">how it was sanitised</a>.",
}


def switch_bar(active):
    """Dataset switch, a one-line explainer and the sign-up link. Plain links: no script, nothing to hash."""
    tab = lambda key, href, text: (f'<a href="{href}"' + (' aria-current="page"' if key == active else "")
                                   + f">{text}</a>")
    return (SWITCH_CSS + '\n<div class="dset" role="navigation" aria-label="Dataset">'
            + '<span class="seg">' + tab("sample", "/", "Sample team")
            + tab("build-record", "/build-record", "How we built Niyantrak") + "</span>"
            + f"<p>{EXPLAIN[active]}</p>"
            + f'<a class="cta" href="{SIGNUP}?src={"demo" if active == "sample" else "build-record"}">Sign up</a>'
            + "</div>\n")


def run(cmd, cwd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def adapt(html, edits, active):
    for old, new in edits:
        if old not in html:
            sys.exit(f"build-demo output changed; cannot find: {old}")
        html = html.replace(old, new)
    assert "</script" not in SERVER_SCAN
    html = html.replace("<body>", "<body>\n" + switch_bar(active), 1)
    return html.replace("</body>", f"<script>\n{SERVER_SCAN}\n</script>\n</body>", 1)


def write(out, rel, html):
    target = os.path.join(out, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(html)
    return target


def seed(src, out, end, seed_no, record=None):
    src = os.path.abspath(src)
    for need in ("tools/demo-data.py", "tools/build-demo.py", "niyantrak/web/standalone-shim.js"):
        if not os.path.exists(os.path.join(src, need)):
            sys.exit(f"{src} is not a niyantrak checkout (missing {need})")
    with tempfile.TemporaryDirectory() as tmp:
        data = os.path.join(tmp, "demo")
        page = os.path.join(tmp, "index.html")
        run([sys.executable, "tools/demo-data.py", "--out", data, "--end", end, "--seed", str(seed_no)], src)
        run([sys.executable, "tools/build-demo.py", page, "--bundle", os.path.join(data, "bundle.json")], src)
        html = adapt(open(page, encoding="utf-8").read(), EDITS, "sample")
        if record:
            rbundle, rpage = os.path.join(tmp, "record.json"), os.path.join(tmp, "record.html")
            run([sys.executable, os.path.join(HERE, "record_bundle.py"), "--src", src, "--data",
                 os.path.abspath(record), "--out", rbundle], src)
            run([sys.executable, "tools/build-demo.py", rpage, "--bundle", rbundle], src)
            rhtml = adapt(open(rpage, encoding="utf-8").read(), RECORD_EDITS, "build-record")
    if "Sample data" not in html:
        sys.exit("demo page is missing the 'Sample data' label")
    target = write(out, "index.html", html)
    print(f"wrote {target} ({len(html) // 1024} KB, sample data ending {end})")
    if record:
        if "Real build record" not in rhtml:
            sys.exit("build-record page is missing the 'Real build record' label")
        target = write(out, os.path.join("build-record", "index.html"), rhtml)
        print(f"wrote {target} ({len(rhtml) // 1024} KB, real build record)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, help="a checkout of github.com/vigyanbytes/niyantrak")
    ap.add_argument("--out", default="site")
    ap.add_argument("--end", default="", help="last day of sample activity (default: today, UTC)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--record", default="", help="demo/build-record (from tools/build-record.py); omit to skip that page")
    a = ap.parse_args()
    seed(a.src, a.out, a.end or str(datetime.now(timezone.utc).date()), a.seed, a.record or None)
