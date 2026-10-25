"""Tests for the build record: the extractor's sanitisation, the committed data, the dataset switch and the server.

    NIYANTRAK_SRC=../niyantrak python3 -m unittest discover -s tests -v

Tests that need Niyantrak itself (its validator, the server's recognizers) are skipped without NIYANTRAK_SRC. The
private forbidden-terms files (~/.config/niyantrak-demo/forbidden*) are used when present and never read into output.
"""
import glob
import http.client
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.path.join(ROOT, "demo", "build-record")
SRC = os.environ.get("NIYANTRAK_SRC")
PRIVATE = sorted(glob.glob(os.path.expanduser("~/.config/niyantrak-demo/forbidden*")))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


br = load("build_record", "tools/build-record.py")
seed = load("seed", "demo/seed.py")
EVENTS = json.load(open(os.path.join(DATA, "events.json"), encoding="utf-8"))
DECISIONS = [json.loads(line) for line in open(os.path.join(DATA, "decisions.jsonl"), encoding="utf-8")]
LABELS = json.load(open(os.path.join(DATA, "labels.json"), encoding="utf-8"))
FILES = {n: open(os.path.join(DATA, n), encoding="utf-8").read() for n in ("events.json", "decisions.jsonl", "labels.json")}


class CommittedData(unittest.TestCase):
    def test_labels_come_from_the_allow_lists(self):
        whats = {w for w, _ in br.TOOLS.values()} | {w for _, (w, _) in br.MCP} | {"Other tool", "Commit", "Proof run"}
        whats |= {r["what"] for r in br.REPORTED}
        for e in EVENTS:
            self.assertIn(e["who"], br.WHO)
            self.assertIn(e["machine"], br.MACHINES + (None,))
            self.assertIn(e["which"], br.PUBLIC_REPOS + (br.PRIVATE, None))
            self.assertIn(e["agent"], ("Claude Code", "not recorded", None))
            self.assertIsNone(e["whom"])
            self.assertEqual(e["entity_counts"], {})
            if e["what"] not in whats:        # only the founder's launch decisions pass through from the store
                self.assertEqual((e["who"], e["rule_ids"]), ("founder", ["niyantrak-launch"]), e["what"])

    def test_no_paths_addresses_or_emails(self):
        for name, text in FILES.items():
            self.assertIsNone(br.STRICT.search(text), name)

    @unittest.skipUnless(PRIVATE, "private forbidden-terms files not present")
    def test_page_text_has_no_private_terms(self):
        for path in PRIVATE:
            rx = re.compile(open(path, encoding="utf-8").read().strip(), re.I)
            for active in ("sample", "build-record"):
                self.assertIsNone(rx.search(seed.switch_bar(active)), f"{active}: {os.path.basename(path)}")

    def test_all_private_terms_files_are_required(self):
        with tempfile.TemporaryDirectory() as d:
            only = os.path.join(d, "forbidden")
            with open(only, "w") as f:
                f.write("zed")
            with self.assertRaises(SystemExit) as cm:
                br.main(["--src", ".", "--sources", os.path.join(d, "none.json"), "--forbidden", only])
        self.assertIn("forbidden-people", str(cm.exception))
        self.assertIn("forbidden-topics", str(cm.exception))

    def test_no_machine_wide_gate_log(self):
        # the gate log cannot tell projects apart, so the record's gate blocks come only from the agents' reports
        self.assertFalse([e for e in EVENTS if e["which"] is None and e["who"] != "founder"])
        blocked = [e for e in EVENTS if e["outcome"] == "blocked"]
        self.assertTrue(blocked)
        self.assertTrue(all(e["ts"][11:13] == "09" for e in blocked))

    @unittest.skipUnless(PRIVATE, "private forbidden-terms files not present")
    def test_no_private_terms(self):
        for path in PRIVATE:
            rx = re.compile(open(path, encoding="utf-8").read().strip(), re.I)
            for name, text in FILES.items():
                self.assertIsNone(rx.search(text), f"{name} matches a term from {os.path.basename(path)}")

    def test_ids_stable_unique_and_time_ordered(self):
        ids = [e["event_id"] for e in EVENTS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids))
        self.assertEqual([e["ts"] for e in EVENTS], sorted(e["ts"] for e in EVENTS))
        again = br.with_ids([{k: v for k, v in e.items() if k != "event_id"} for e in reversed(EVENTS)])
        self.assertEqual([e["event_id"] for e in again], ids)

    def test_labels_file_matches_the_events(self):
        self.assertEqual(LABELS["labels"], json.loads(json.dumps(br.labels(EVENTS, DECISIONS))))
        self.assertEqual(LABELS["meta"]["events"], len(EVENTS))

    def test_gate_blocks_and_disclosed_slips_are_in_the_record(self):
        out = {}
        for e in EVENTS:
            out[e["outcome"]] = out.get(e["outcome"], 0) + 1
        self.assertEqual(out.get("blocked"), 10)
        self.assertEqual(sum("disclosed-slip" in e["rule_ids"] for e in EVENTS), 3)
        titles = {d["title"] for d in DECISIONS}
        self.assertEqual(sum(t.startswith("Disclosed slip:") for t in titles), 3)
        slips = {d["src"] for d in DECISIONS if d["kind"] == "WORK" and d["title"].startswith("Disclosed slip:")}
        status = {d["src"]: d["title"] for d in DECISIONS if d["kind"] == "STATUS" and d["src"] in slips}
        self.assertEqual(set(status.values()), {"proposed"})   # open until the founder judges them, never hidden as done

    @unittest.skipUnless(SRC, "set NIYANTRAK_SRC to a niyantrak checkout")
    def test_every_event_passes_niyantraks_validator(self):
        sys.path.insert(0, os.path.abspath(SRC))
        from niyantrak.decisions import schema as dschema
        from niyantrak.telemetry.schema import validate
        for e in EVENTS:
            validate(e)
        dschema.check_privacy([dschema.normalize(r, "test", {}) for r in DECISIONS])


class Extractor(unittest.TestCase):
    CFG = {"window": ["2026-10-21", "2026-10-25"], "cutoff": "2026-10-25T11:30:00Z", "agent_trailer": "Co-Authored-By",
           "agent_trailer_match": {"Claude": "Claude Code"}}

    def test_tool_categories(self):
        self.assertEqual(br.tool_label("Bash"), ("Run shell command", "shell"))
        self.assertEqual(br.tool_label("mcp__claude-in-chrome__navigate"), ("Browser action", "browser"))
        self.assertEqual(br.tool_label("mcp__something_new__x"), ("Connector call", "connector"))
        self.assertEqual(br.tool_label("BrandNewTool"), ("Other tool", "other"))

    def test_gate_log_keeps_decisions_and_drops_inputs(self):
        lines = [
            {"ts": 1792800000, "gate": None, "tool": "Bash", "input_sha256": "ab" * 32, "decision": "allowed"},
            {"ts": 1792800060, "gate": "destructive_git", "tool": "Bash", "input_sha256": "cd" * 32, "decision": "blocked"},
            {"ts": 1792800120, "gate": "made_up_gate", "tool": "Write", "decision": "blocked"},
            {"ts": 1700000000, "gate": None, "tool": "Bash", "decision": "allowed"},           # before the window
            {"ts": 1792930000, "gate": None, "tool": "Bash", "decision": "allowed"},           # same day, after the cutoff
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
        try:
            ev = br.from_gate_log({"path": f.name, "machine": "build machine A", "agent": "Claude Code"}, self.CFG)
        finally:
            os.unlink(f.name)
        self.assertEqual([(e["outcome"], e["why"], e["rule_ids"]) for e in ev],
                         [("allowed", "No hard stop", []), ("blocked", "destructive_git hard stop", ["destructive_git"]),
                          ("blocked", "other_gate hard stop", ["other_gate"])])
        self.assertNotIn("abab", json.dumps(ev))
        self.assertTrue(all(e["ts"].endswith(":00Z") for e in ev))     # minute precision only

    def test_commits_count_only(self):
        with tempfile.TemporaryDirectory() as repo:
            git = lambda *a, **env: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True,
                                                   env={**os.environ, **env})
            git("init", "-q")
            when = {"GIT_AUTHOR_DATE": "2026-10-23T10:00:00Z", "GIT_COMMITTER_DATE": "2026-10-23T10:00:00Z",
                    "GIT_AUTHOR_NAME": "Real Person", "GIT_AUTHOR_EMAIL": "real.person@example.org",
                    "GIT_COMMITTER_NAME": "Real Person", "GIT_COMMITTER_EMAIL": "real.person@example.org"}
            git("commit", "-q", "--allow-empty", "-m", "secret plan for client acme\n\nCo-Authored-By: Claude <x@y.z>", **when)
            git("commit", "-q", "--allow-empty", "-m", "hand edit", **when)
            ev = br.from_repo({"path": repo, "which": "private repo"}, self.CFG)
        self.assertEqual(sorted((e["who"], e["agent"]) for e in ev),
                         [("coding agent", "Claude Code"), ("founder", "not recorded")])
        blob = json.dumps(ev)
        for leaked in ("secret plan", "acme", "Real Person", "real.person", "hand edit"):
            self.assertNotIn(leaked, blob)

    def test_leak_check_names_the_file_not_the_value(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stderr(err):
            br.leak_check(["machine was laptop-of-zed"], [("forbidden-test", re.compile("zed"))])
        self.assertIn("forbidden-test", str(cm.exception))
        self.assertNotIn("zed", str(cm.exception))
        with self.assertRaises(SystemExit):
            br.leak_check(["path /home/zed"], [])

    def test_event_refuses_unknown_roles_and_machines(self):
        with self.assertRaises(AssertionError):
            br.event("2026-10-23T00:00:00Z", "zed", "Commit", "git", "x", "allowed")
        with self.assertRaises(AssertionError):
            br.event("2026-10-23T00:00:00Z", "founder", "Commit", "git", "x", "allowed", machine="zeds-laptop")
        with self.assertRaises(AssertionError):
            br.event("2026-10-23T00:00:00Z", "founder", "Commit", "git", "x", "allowed", which="zed/secret-repo")


class DatasetSwitch(unittest.TestCase):
    def test_switch_marks_the_active_dataset_and_links_to_signup(self):
        for active, src in (("sample", "demo"), ("build-record", "build-record")):
            bar = seed.switch_bar(active)
            self.assertIn('href="/"', bar)
            self.assertIn('href="/build-record"', bar)
            self.assertEqual(bar.count('aria-current="page"'), 1)
            self.assertIn(f'href="{seed.SIGNUP}?src={src}"', bar)
            self.assertNotIn("<script", bar)
        self.assertIn("where we slipped and disclosed it", seed.switch_bar("build-record"))
        self.assertIn('href="/build-record" aria-current="page"', seed.switch_bar("build-record"))


@unittest.skipUnless(SRC, "set NIYANTRAK_SRC to a niyantrak checkout")
class Server(unittest.TestCase):
    def test_build_record_page_has_its_own_csp(self):
        sys.path.insert(0, os.path.abspath(SRC))
        server = load("demo_server", "demo/server.py")
        with open(os.path.join(ROOT, "demo", "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        pages = {"/": b"<html><body>Sample data<script>var a=1;</script></body></html>",
                 "/build-record": b"<html><body>Real build record<script>var b=2;</script></body></html>"}
        httpd = server.Server(("127.0.0.1", 0), server.make_handler(cfg, pages, server.Limiter(cfg["rate_limit"])))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            got = {}
            for path in ("/", "/build-record", "/build-record/", "/index.html", "/nope"):
                c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
                c.request("GET", path)
                r = c.getresponse()
                got[path] = (r.status, r.getheader("Content-Security-Policy"), r.read())
                c.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(got["/"][0], 200)
        self.assertIn(b"Real build record", got["/build-record"][2])
        self.assertEqual(got["/build-record/"][2], got["/build-record"][2])
        self.assertEqual(got["/index.html"][2], got["/"][2])
        self.assertNotEqual(got["/"][1], got["/build-record"][1])
        self.assertEqual(got["/nope"][0], 404)


if __name__ == "__main__":
    unittest.main()
