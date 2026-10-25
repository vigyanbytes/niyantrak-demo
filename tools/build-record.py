#!/usr/bin/env python3
"""build-record — VigyanBytes' own build telemetry -> the demo's second dataset, "How we built Niyantrak".

    python3 tools/build-record.py --src <niyantrak checkout> [--sources FILE] [--out demo/build-record] \
        [--forbidden FILE ...] [--check]

Niyantrak was built by AI coding agents under our own autonomy gate. This script turns that real record into 6W
events (niyantrak.6w/1) and decision-log items that the public demo shows next to the fictional sample team.

Inputs are private and never committed; their locations come from --sources (default
~/.config/niyantrak-demo/build-record-sources.json):
    gate_logs   optional, and not used for the published record: the autonomy gate's machine-wide log (tool name,
                gate, decision, input hash). It cannot tell which project a call belonged to, so the record takes its
                gate blocks from the Niyantrak agents' own reports instead (REPORTED below)
    store       a Niyantrak 6W store; only events tagged with the launch rule are used (the founder's decisions)
    repos       git checkouts; per commit only the author date and whether it carries an agent co-author trailer
                (no message, no author, no file); a proof history (date + test counts) where named
    cutoff      nothing after this instant is read, so a re-run gives the same output byte for byte

Sanitisation is allow-list based. A published label is either a fixed value in this file or a value read from the
store that passes the 6W validator and the forbidden-terms check:
    who      founder | coding agent | reviewer agent              (never a person's name or login)
    machine  build machine A | Windows test laptop                (never a hostname or address)
    which    the public repository name for public repos, "private repo" otherwise, null when not recorded
    what     a tool category (Run shell command, Edit file, ...), never a command, path, message or file content
Everything is validated with Niyantrak's own validator (it rejects identifiers) and every output file is checked
against a built-in strict list and the private terms files (servers, people and excluded topics) (regex, |-joined; default ~/.config/niyantrak-demo/forbidden*).

Writes, under --out: events.json, decisions.jsonl, labels.json (every distinct published label value, for review).
--check regenerates in memory and fails if the committed files differ.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

CONFIG = os.path.expanduser("~/.config/niyantrak-demo")
HERE = os.path.dirname(os.path.abspath(__file__))
LABEL = "Real build record — sanitised: role labels, generic machines, counts only"

WHO = ("founder", "coding agent", "reviewer agent")
MACHINES = ("build machine A", "Windows test laptop")
PUBLIC_REPOS = ("vigyanbytes/niyantrak", "vigyanbytes/niyantrak-demo", "vigyanbytes/niyantrak-keys")
PRIVATE = "private repo"
GATES = ("destructive_git", "destructive_command", "production_deploy", "secret_access")
TOOLS = {   # tool name -> (what, where)
    "Bash": ("Run shell command", "shell"), "run_command": ("Run shell command", "shell"),
    "Read": ("Read file", "workspace files"), "Edit": ("Edit file", "workspace files"),
    "apply_patch": ("Edit file", "workspace files"), "NotebookEdit": ("Edit file", "workspace files"),
    "Write": ("Write file", "workspace files"),
    "WebSearch": ("Web search", "public web"), "WebFetch": ("Read web page", "public web"),
    "Agent": ("Coordinate agent team", "agent team"), "SendMessage": ("Coordinate agent team", "agent team"),
    "SubagentHandback": ("Coordinate agent team", "agent team"), "TaskStop": ("Coordinate agent team", "agent team"),
    "ToolSearch": ("Load tool or skill", "Claude Code"), "Skill": ("Load tool or skill", "Claude Code"),
    "AskUserQuestion": ("Talk to the founder", "Claude Code"), "PushNotification": ("Talk to the founder", "Claude Code"),
    "SendFeedback": ("Talk to the founder", "Claude Code"), "SendUserFile": ("Talk to the founder", "Claude Code"),
    "Artifact": ("Publish private page", "claude.ai"),
}
MCP = (   # tool-name prefix -> (what, where)
    ("mcp__claude-in-chrome__", ("Browser action", "browser")),
    ("mcp__coolify__", ("Hosting panel call", "hosting control panel")),
    ("mcp__searxng__", ("Web search", "public web")),
    ("mcp__whatsapp-business-tools__", ("WhatsApp Business call", "WhatsApp Business API")),
    ("mcp__claude_ai_Gmail__", ("Google Workspace call", "Google Workspace")),
    ("mcp__claude_ai_Google_Drive__", ("Google Workspace call", "Google Workspace")),
    ("mcp__", ("Connector call", "connector")),
)

# Governance moments taken from the reports of the agents that built Niyantrak (its client, dashboard, demo, keys
# CLI, installer and social tooling): every gate block and permission prompt they disclosed, the founder's approval,
# the review and the slips. Day only, so they are shown from 09:00 UTC. Each is a real event; the wording is ours and
# carries no names, hosts or paths. The machine-wide gate log is deliberately not used: it cannot tell which project
# a tool call belonged to.
REPORTED = [
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A", what="Edit docs",
         where="workspace files", which="vigyanbytes/niyantrak", why="destructive_git hard stop (false positive)",
         outcome="blocked", rule_ids=["destructive_git"], minute=1),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A",
         what="Create GitHub release", where="github", which="vigyanbytes/niyantrak", why="production_deploy hard stop",
         outcome="blocked", rule_ids=["production_deploy"], minute=2),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A", what="Write Dockerfile",
         where="workspace files", which="vigyanbytes/niyantrak-demo", why="destructive_command hard stop",
         outcome="blocked", rule_ids=["destructive_command"], minute=3),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A",
         what="Test deploy script on a mock", where="shell", which="vigyanbytes/niyantrak-demo",
         why="production_deploy hard stop", outcome="blocked", rule_ids=["production_deploy"], minute=4),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A",
         what="Force-push to fix commit author", where="git", which=PRIVATE, why="destructive_git hard stop",
         outcome="blocked", rule_ids=["destructive_git"], minute=6),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A", what="Write Dockerfile",
         where="workspace files", which=PRIVATE, why="destructive_command hard stop", outcome="blocked",
         rule_ids=["destructive_command"], minute=7),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A",
         what="Write outside its worktree", where="workspace files", which=PRIVATE,
         why="worktree isolation: refused by the harness", outcome="blocked", rule_ids=["worktree-isolation"], minute=8),
    dict(day="2026-10-25", who="coding agent", agent="Claude Code", machine="build machine A", what="Write docs file",
         where="workspace files", which="vigyanbytes/niyantrak-keys", why="destructive_git hard stop (false positive)",
         outcome="blocked", rule_ids=["destructive_git"], minute=1),
    dict(day="2026-10-25", who="coding agent", agent="Claude Code", machine="build machine A", what="Write CI workflow",
         where="workspace files", which="vigyanbytes/niyantrak-keys", why="production_deploy hard stop (false positive)",
         outcome="blocked", rule_ids=["production_deploy"], minute=2),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A", what="Export public tree",
         where="shell", which="vigyanbytes/niyantrak", why="permission prompt: recursive delete in one command",
         outcome="needs_approval", rule_ids=["permission-prompt"]),
    dict(day="2026-10-23", who="founder", agent="Claude Code", machine="build machine A", what="Export public tree",
         where="shell", which="vigyanbytes/niyantrak", why="approved as plain separate steps, no recursive delete",
         outcome="approved", rule_ids=["permission-prompt"], minute=5),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="Windows test laptop",
         what="Delete untracked files", where="shell", which=PRIVATE, why="destructive_git hard stop",
         outcome="blocked", rule_ids=["destructive_git"]),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A",
         what="Rewrote file without blocked line", where="workspace files", which="vigyanbytes/niyantrak-demo",
         why="disclosed: continued after a block instead of stopping", outcome="allowed",
         rule_ids=["destructive_command", "disclosed-slip"], minute=10),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A",
         what="Commit message from file", where="git", which="vigyanbytes/niyantrak-demo",
         why="disclosed: wording kept the deploy matcher from firing", outcome="allowed",
         rule_ids=["production_deploy", "disclosed-slip"], minute=20),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A",
         what="Rewrote file without blocked line", where="workspace files", which=PRIVATE,
         why="disclosed: continued after a block instead of stopping", outcome="allowed",
         rule_ids=["destructive_command", "disclosed-slip"], minute=30),
    dict(day="2026-10-23", who="reviewer agent", agent="Claude Code", machine="build machine A",
         what="Review public exposure", where="shell", which=PRIVATE,
         why="gaps before going public: body cap, rate limit, HSTS", outcome="needs_approval",
         rule_ids=["review"], minute=40),
    dict(day="2026-10-23", who="coding agent", agent="Claude Code", machine="build machine A",
         what="Fix review findings", where="workspace files", which=PRIVATE, why="every gap closed and retested",
         outcome="approved", rule_ids=["review"], minute=50),
]

D = lambda day, h=12: f"{day}T{h:02d}:00:00.000000000"
THINGS = [("niyantrak", "Niyantrak free edition"), ("niyantrak-demo", "Public demo"),
          ("agent-gate", "Autonomy gate on the build machines"), ("dashboard", "Niyantrak Dashboard (Pro)"),
          ("client", "Niyantrak client: keys and Windows app")]
DECISIONS = [   # (id, day, who, title, text, about)
    ("BR-DEC-01", "2026-10-23", "founder", "Who acted is derived from the operating system, never typed",
     "The login record and the system's user directory give the name; a stable directory id survives renames. "
     "Agents are recorded as acting for that person.", "niyantrak"),
    ("BR-DEC-02", "2026-10-23", "founder", "Agents never change their own gate",
     "Changing the gate is self-modification and is refused. Restarts, staging and gate changes stay with the "
     "founder.", "agent-gate"),
    ("BR-DEC-03", "2026-10-23", "founder", "After a gate block the agent stops: no other tool, no rewording",
     "Set mid-launch, after two agents had continued past a block (see the disclosed slips).", "agent-gate"),
    ("BR-DEC-04", "2026-10-23", "founder", "Public history starts fresh at v0.1.0",
     "A history scan found a personal email on nine early commits, so the public repository was exported fresh "
     "under the company identity. The leak scan runs on the tree and the history before every push.", "niyantrak"),
    ("BR-DEC-05", "2026-10-23", "founder", "Free edition under Apache-2.0; Pro, Enterprise and Government are commercial",
     None, "niyantrak"),
    ("BR-DEC-06", "2026-10-23", "founder", "Contributions under the DCO, no CLA", None, "niyantrak"),
    ("BR-DEC-07", "2026-10-23", "founder", "Releases and deploys are the founder's to run",
     "The gate blocked release creation and deploy calls. The agents prepared the commands and artifacts and "
     "stopped there.", "agent-gate"),
    ("BR-DEC-08", "2026-10-23", "founder", "The public demo shows only fictional or sanitised data",
     "The smoke test fails if a public response carries a real hostname or a private address.", "niyantrak-demo"),
    ("BR-DEC-09", "2026-10-25", "founder", "6W: record which project, repository or workspace", None, "niyantrak"),
    ("BR-DEC-10", "2026-10-25", "founder", "This build record is published with role labels only",
     "Who is a role (founder, coding agent, reviewer agent), machines are generic, private repositories are "
     "'private repo'. Counts only: never commit messages, commands, paths or file contents.", "niyantrak-demo"),
]
WORK = [   # (id, day, who, title, text, statuses, references)
    ("BR-WORK-01", "2026-10-23", "coding agent", "6W labels accept real paths",
     "Found while recording operations: the store refused labels that start with a slash.", ["done"], "niyantrak"),
    ("BR-WORK-02", "2026-10-23", "coding agent", "Health identifier pack: NHS, NPI, KVNR, MBI, ABHA",
     "59 new tests; every health value is rejected in the 6W labels.", ["done"], "niyantrak"),
    ("BR-WORK-03", "2026-10-23", "coding agent", "Gate hook files checked against the real agent CLIs",
     "Codex runs a new hook only after a person reviews it. Documented, not bypassed.", ["done"], "agent-gate"),
    ("BR-WORK-04", "2026-10-23", "reviewer agent", "Dashboard public-exposure guards: body cap, rate limits, HSTS",
     "A reviewer agent found five gaps before anything went public; all closed and retested.", ["done"], "dashboard"),
    ("BR-WORK-05", "2026-10-23", "coding agent", "Windows client build and signed test package",
     "Rust and app tests passed on the Windows test laptop; the test certificate was removed afterwards.",
     ["done"], "client"),
    ("BR-WORK-06", "2026-10-23", "coding agent", "Tidy the stale clone on the Windows test laptop",
     "The gate blocked the agent's git clean. Left for the founder.", ["proposed"], "client"),
    ("BR-WORK-07", "2026-10-23", "coding agent", "Compile the desktop app shell on Linux",
     "Needs system packages; installs go through the governed installer with the founder's yes.", ["blocked"],
     "client"),
    ("BR-WORK-08", "2026-10-23", "coding agent", "Windows demo executable",
     "Build script prepared and tested off Windows; not run on any machine without the founder's yes.",
     ["on-hold"], "niyantrak"),
    ("BR-WORK-09", "2026-10-23", "coding agent", "Create the GitHub releases v0.1.0 and v0.1.1",
     "Agents may not create releases; artifacts and the command are ready for the founder.", ["proposed"],
     "niyantrak"),
    ("BR-WORK-10", "2026-10-23", "coding agent", "Public demo goes live",
     "Built and smoke-tested; hosting and DNS steps wait for the founder.", ["proposed"], "niyantrak-demo"),
    ("BR-SLIP-01", "2026-10-23", "coding agent", "Disclosed slip: file rewritten after a block (demo), founder to judge",
     "The gate blocked a heredoc that contained a recursive delete. The agent wrote the file again without that line "
     "instead of stopping. Nothing was deleted; the agent disclosed it for the founder to judge.", ["proposed"],
     "niyantrak-demo"),
    ("BR-SLIP-02", "2026-10-23", "coding agent", "Disclosed slip: commit message kept a keyword matcher quiet, founder to judge",
     "After a deploy block, a commit message naming the hosting tool was written to a file and committed from it, "
     "partly so the matcher would not fire. That commit deployed nothing; the agent flagged it as the pattern the "
     "rule forbids.", ["proposed"], "niyantrak-demo"),
    ("BR-SLIP-03", "2026-10-23", "coding agent", "Disclosed slip: file rewritten after a block (dashboard), founder to judge",
     "Same pattern as BR-SLIP-01 in the dashboard build: a cache clean-up line was dropped after the block. "
     "Disclosed in the agent's report.", ["proposed"], "dashboard"),
    ("BR-WORK-11", "2026-10-23", "coding agent", "Gate false positive on a docs edit",
     "A docs edit that only mentioned a history-rewrite tool was blocked. No git command was involved; the agent "
     "checked the reflog afterwards to confirm nothing was rewritten.", ["done"], "agent-gate"),
]


def utc(s):
    s = s.strip()
    d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def iso(d):
    return d.astimezone(timezone.utc).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def event(ts, who, what, where, why, outcome, agent=None, machine=None, which=None, rule_ids=(), whom=None):
    assert who in WHO and (machine is None or machine in MACHINES), (who, machine)
    assert which is None or which in PUBLIC_REPOS + (PRIVATE,), which
    return {"ts": ts, "who": who, "whom": whom, "agent": agent, "what": what, "where": where, "which": which,
            "why": why, "outcome": outcome, "rule_ids": list(rule_ids), "machine": machine, "entity_counts": {}}


def tool_label(name):
    if name in TOOLS:
        return TOOLS[name]
    return next((v for p, v in MCP if name.startswith(p)), ("Other tool", "other"))


def in_window(d, cfg):
    lo, hi = cfg["window"]
    return lo <= d.strftime("%Y-%m-%d") <= hi and d <= utc(cfg["cutoff"])


def from_gate_log(src, cfg):
    out = []
    with open(os.path.expanduser(src["path"]), encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
                d = datetime.fromtimestamp(int(e["ts"]), timezone.utc)
            except (ValueError, KeyError, TypeError):
                continue
            if not in_window(d, cfg) or e.get("decision") not in ("allowed", "blocked"):
                continue
            what, where = tool_label(str(e.get("tool") or ""))
            gate = e.get("gate") if e.get("gate") in GATES else ("other_gate" if e.get("gate") else None)
            blocked = e["decision"] == "blocked"
            out.append(event(iso(d), "coding agent", what, where, f"{gate} hard stop" if blocked else "No hard stop",
                             "blocked" if blocked else "allowed", agent=src["agent"], machine=src["machine"],
                             rule_ids=[gate] if gate else []))
    return out


def from_store(st, cfg):
    import duckdb   # build time only, like the demo's seed
    files = glob.glob(os.path.join(st["path"], "segments", "*", "*", "*.parquet"))
    if not files:
        return []
    rows = duckdb.connect().execute(
        "SELECT ts, what, \"where\", why, outcome FROM read_parquet(?) WHERE list_contains(rule_ids, ?) ORDER BY ts",
        [files, st["rule"]]).fetchall()
    out = []
    for ts, what, where, why, outcome in rows:
        d = ts.replace(tzinfo=timezone.utc)
        if in_window(d, cfg) and outcome in ("needs_approval", "approved", "rejected"):
            out.append(event(iso(d), "founder", what, where, why, outcome, which=st["which"], rule_ids=[st["rule"]]))
    return out


def from_repo(repo, cfg):
    out, path = [], os.path.expanduser(repo["path"])
    lo, hi = cfg["window"]
    fmt = "%H%x09%aI%x09%(trailers:key=" + cfg["agent_trailer"] + ",valueonly,separator=;)"
    log = subprocess.run(["git", "-C", path, "log", "--all", "--since", lo + "T00:00:00Z", "--format=" + fmt],
                         check=True, capture_output=True, text=True).stdout
    seen = set()
    for line in log.splitlines():
        h, date, trailer = (line.split("\t") + ["", ""])[:3]
        if h in seen:
            continue
        seen.add(h)
        d = utc(date)
        if not in_window(d, cfg):
            continue
        agent = next((v for k, v in cfg["agent_trailer_match"].items() if k in trailer), None)
        out.append(event(iso(d), "coding agent" if agent else "founder", "Commit", "git",
                         "agent co-author trailer on commit" if agent else "no agent trailer on commit", "allowed",
                         agent=agent or "not recorded", which=repo["which"]))
    if repo.get("proof_history"):
        rev, name = repo["proof_history"].split(":", 1)
        hist = subprocess.run(["git", "-C", path, "show", f"{rev}:{name}"], check=True, capture_output=True,
                              text=True).stdout
        for line in hist.splitlines():
            p = json.loads(line)
            d = utc(p["date"])
            if in_window(d, cfg):
                why = (f"{int(p['passed'])} passed {int(p['failed'])} failed {int(p['skipped'])} skipped, "
                       f"leak scan {'clean' if p.get('leak_scan') == 'clean' else 'not clean'}")
                ok = not p["failed"] and p.get("leak_scan") == "clean"
                out.append(event(iso(d), "coding agent", "Proof run", "test suite", why,
                                 "allowed" if ok else "error", agent="Claude Code",
                                 machine="build machine A", which=repo["which"], rule_ids=["proof"]))
    return out


def reported():
    out = []
    for r in REPORTED:
        ts = f"{r['day']}T09:{r.get('minute', 0):02d}:00Z"
        out.append(event(ts, r["who"], r["what"], r["where"], r["why"], r["outcome"], agent=r["agent"],
                         machine=r["machine"], which=r["which"], rule_ids=r["rule_ids"]))
    return out


def decisions():
    rows = []
    def add(kind, src, title, ts, who, text=None, dst=None):
        r = {"kind": kind, "src": src, "title": title, "ts": ts, "session_id": "build-record", "user_id": who,
             "agent_id": "Claude Code", "product": "niyantrak"}
        if text:
            r["text"] = text
        if dst:
            r["dst"] = dst
        rows.append(r)
    for src, title in THINGS:
        add("THING", src, title, D("2026-10-21", 8), "founder")
    for src, day, who, title, text, about in DECISIONS:
        add("DECISION", src, title, D(day), who, text)
        add("ABOUT", src, f"{src} about {about}", D(day, 13), who, dst=about)
    for src, day, who, title, text, steps, about in WORK:
        add("WORK", src, title, D(day), who, text)
        for k, word in enumerate(steps):
            add("STATUS", src, word, D(day, 15 + k), who)
        add("REFERENCES", src, f"{src} references {about}", D(day, 14), who, dst=about)
    for src, dst in (("BR-SLIP-01", "BR-DEC-03"), ("BR-SLIP-02", "BR-DEC-03"), ("BR-SLIP-03", "BR-DEC-03"),
                     ("BR-WORK-09", "BR-DEC-07"), ("BR-WORK-10", "BR-DEC-07"), ("BR-WORK-06", "BR-DEC-07")):
        add("REFERENCES", src, f"{src} references {dst}", D("2026-10-23", 16), "founder", dst=dst)
    return rows


def with_ids(events):
    """Sort and give each event a stable id: same inputs -> same ids."""
    events = sorted(events, key=lambda e: json.dumps(e, sort_keys=True))
    events.sort(key=lambda e: e["ts"])
    seen = {}
    for e in events:
        key = json.dumps(e, sort_keys=True)
        seen[key] = seen.get(key, 0) + 1
        e["event_id"] = hashlib.sha256(f"{key}#{seen[key]}".encode()).hexdigest()[:32]
    return events


def forbidden_terms(files):
    terms = []
    for path in files:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            t = f.read().strip()
        if t:
            terms.append((os.path.basename(path), re.compile(t, re.I)))
    return terms


# Private terms files that must all be present: servers and addresses, people, and topics that must never be
# published. Their contents are never committed and never printed.
REQUIRED_TERMS = ("forbidden", "forbidden-people", "forbidden-topics")
# Never in the published record, whatever the private files say: home folders, addresses, emails, tailnet names.
STRICT = re.compile(r"/home/|/Users/|[A-Za-z]:[\\/]Users|\b\d{1,3}(?:\.\d{1,3}){3}\b|[\w.+-]+@[\w-]+\.\w+|\.ts\.net\b"
                    r"|\bsrv\d{3,}\b", re.I)


def leak_check(texts, terms):
    """Fails on any forbidden term; the message names the terms file, never the value."""
    bad = sorted({name for name, rx in [("built-in strict list", STRICT)] + terms for t in texts if rx.search(t)})
    if bad:
        sys.exit(f"build-record: output matches a forbidden term from {', '.join(bad)}; nothing written")


def labels(events, decs):
    inv = {}
    for f in ("who", "whom", "agent", "what", "where", "why", "which", "machine", "outcome"):
        c = {}
        for e in events:
            v = e.get(f)
            c[str(v) if v is not None else "(not recorded)"] = c.get(str(v) if v is not None else "(not recorded)", 0) + 1
        inv[f] = dict(sorted(c.items()))
    inv["rule_ids"] = dict(sorted({r: sum(r in e["rule_ids"] for e in events) for e in events for r in e["rule_ids"]}.items()))
    inv["decision_users"] = sorted({d["user_id"] for d in decs})
    inv["decision_agents"] = sorted({d["agent_id"] for d in decs})
    inv["decision_titles"] = sorted({d["title"] for d in decs if d["kind"] in ("DECISION", "WORK", "THING")})
    return inv


def generate(src, cfg):
    sys.path.insert(0, os.path.abspath(src))
    from niyantrak.decisions import schema as dschema
    from niyantrak.telemetry.schema import validate
    ev = []
    for g in cfg.get("gate_logs", []):
        ev += from_gate_log(g, cfg)
    if cfg.get("store"):
        ev += from_store(cfg["store"], cfg)
    for r in cfg["repos"]:
        ev += from_repo(r, cfg)
    ev = with_ids(ev + reported())
    for e in ev:
        validate(e)               # Niyantrak's own validator: rejects identifiers, free text and odd characters
    decs = decisions()
    norm = [dschema.normalize(r, "build-record", {}) for r in decs]
    dschema.check_privacy(norm)
    return ev, decs


def render(ev, decs, cfg):
    meta = {"label": LABEL, "window": cfg["window"], "cutoff": cfg["cutoff"], "events": len(ev),
            "decision_rows": len(decs)}
    return {
        "events.json": json.dumps(ev, indent=1, sort_keys=True) + "\n",
        "decisions.jsonl": "".join(json.dumps(r, sort_keys=True) + "\n" for r in decs),
        "labels.json": json.dumps({"meta": meta, "labels": labels(ev, decs)}, indent=1, sort_keys=True,
                                  ensure_ascii=False) + "\n",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, help="a checkout of github.com/vigyanbytes/niyantrak (v0.1.1+)")
    ap.add_argument("--sources", default=os.path.join(CONFIG, "build-record-sources.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "demo", "build-record"))
    ap.add_argument("--forbidden", nargs="*", default=sorted(glob.glob(os.path.join(CONFIG, "forbidden*"))))
    ap.add_argument("--check", action="store_true", help="fail if the committed files differ from a fresh run")
    a = ap.parse_args(argv)
    terms = forbidden_terms(a.forbidden)
    missing = sorted(set(REQUIRED_TERMS) - {name for name, _ in terms})
    if missing:
        sys.exit(f"build-record: private terms file(s) missing: {', '.join(missing)}; refusing to write real data")
    with open(os.path.expanduser(a.sources), encoding="utf-8") as f:
        cfg = json.load(f)
    ev, decs = generate(a.src, cfg)
    files = render(ev, decs, cfg)
    leak_check(files.values(), terms)
    if a.check:
        diff = [n for n, t in files.items()
                if not os.path.exists(os.path.join(a.out, n)) or open(os.path.join(a.out, n), encoding="utf-8").read() != t]
        if diff:
            sys.exit(f"build-record: committed files differ from a fresh run: {', '.join(diff)}")
        print(f"build-record: up to date ({len(ev)} events, {len(decs)} decision-log rows)")
        return 0
    os.makedirs(a.out, exist_ok=True)
    for n, t in files.items():
        with open(os.path.join(a.out, n), "w", encoding="utf-8") as f:
            f.write(t)
    print(f"build-record: {len(ev)} 6W events, {len(decs)} decision-log rows, "
          f"{cfg['window'][0]} to {cfg['window'][1]} (cutoff {cfg['cutoff']}) -> {os.path.normpath(a.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
