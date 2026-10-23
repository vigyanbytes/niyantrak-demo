"""Niyantrak public demo server — read-only, standard library only.

    python3 demo/server.py [--site site] [--config demo/config.json] [--port 8080]

Serves the demo page built by demo/seed.py (fictional sample team) and one live endpoint:

    GET  /             the panel in demo mode (sample data, labelled "Sample data")
    GET  /healthz      {"ok": true, ...}
    POST /api/scan     {"text": "..."} -> {"masked": "...", "counts": {...}} using Niyantrak's real recognizers.
                       The text is held in memory for the length of the request only: never stored, never logged.

Everything else that writes is refused: POST elsewhere -> 403, PUT/PATCH/DELETE/OPTIONS/... -> 405.
Scan calls are rate-limited per client and globally. Request logs carry method, path (no query), status and
duration only — no body, no query string, no IP address, no user agent.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from niyantrak import __version__ as NIYANTRAK_VERSION  # noqa: E402
from niyantrak.cli import detect, preview  # noqa: E402
from niyantrak.recognizers import PACKS  # noqa: E402

READ_ONLY_MSG = "This is a read-only public demo. Install Niyantrak to use this action: https://github.com/vigyanbytes/niyantrak"
WRITE_METHODS = ("PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT", "PROPFIND")


class Bucket:
    """Token bucket: `rate` tokens per minute, at most `burst` saved up."""
    __slots__ = ("tokens", "stamp")

    def __init__(self, burst):
        self.tokens, self.stamp = float(burst), time.monotonic()

    def take(self, rate, burst):
        now = time.monotonic()
        self.tokens = min(burst, self.tokens + (now - self.stamp) * rate / 60.0)
        self.stamp = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class Limiter:
    """Per-client and global scan limits. Clients are keyed by a salted hash of their address, kept in memory only."""

    def __init__(self, cfg):
        self.rate = cfg["scan_per_client_per_minute"]
        self.burst = cfg["scan_burst_per_client"]
        self.global_rate = cfg["scan_global_per_minute"]
        self.max_clients = cfg["max_tracked_clients"]
        self.salt = os.urandom(16)
        self.clients = OrderedDict()
        self.everyone = Bucket(self.global_rate)
        self.lock = threading.Lock()

    def allow(self, client):
        key = hashlib.sha256(self.salt + client.encode()).digest()
        with self.lock:
            b = self.clients.pop(key, None) or Bucket(self.burst)
            self.clients[key] = b
            while len(self.clients) > self.max_clients:
                self.clients.popitem(last=False)
            if not b.take(self.rate, self.burst):
                return False
            return self.everyone.take(self.global_rate, self.global_rate)


def build_csp(cfg, html):
    """CSP from config; '@inline-script-hashes' becomes one sha256 source per inline <script> in the page."""
    hashes = ["'sha256-%s'" % base64.b64encode(hashlib.sha256(m.encode()).digest()).decode()
              for m in re.findall(r"<script>(.*?)</script>", html, re.S)]
    parts = []
    for name, sources in cfg.items():
        out = []
        for s in sources:
            out += hashes if s == "@inline-script-hashes" else [s]
        parts.append(name + " " + " ".join(out))
    return "; ".join(parts)


def make_handler(cfg, page, limiter):
    csp = build_csp(cfg["csp"], page.decode())
    common = dict(cfg["headers"], **{"Content-Security-Policy": csp})
    health = json.dumps({"ok": True, "mode": "read-only demo", "niyantrak": NIYANTRAK_VERSION}).encode()
    max_body, max_text, log_requests = cfg["max_body_bytes"], cfg["max_text_chars"], cfg["logging"]["requests"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "niyantrak-demo"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = cfg["request_timeout_seconds"]

        def log_message(self, fmt, *args):   # stdlib default would log the client address and full request line
            pass

        def log_request(self, code="-", size="-"):
            if log_requests:
                path = (self.path or "").split("?", 1)[0][:200]
                ms = int((time.monotonic() - getattr(self, "_t0", time.monotonic())) * 1000)
                sys.stderr.write("%s %s %s %dms\n" % (self.command, path, code, ms))

        def parse_request(self):
            self._t0 = time.monotonic()
            return super().parse_request()

        def _send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store", extra=None):
            self.send_response(code)
            for k, v in common.items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", cache)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            if code != 204:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj, **kw):
            self._send(code, json.dumps(obj).encode(), **kw)

        def _client(self):
            ip = self.headers.get("CF-Connecting-IP") or (self.headers.get("X-Forwarded-For") or "").split(",")[0]
            return ip.strip() or self.client_address[0]

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8", cache="no-cache")
            if path == "/healthz":
                return self._send(200, health)
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon", cache="public, max-age=86400")
            if path == "/robots.txt":
                return self._send(200, b"User-agent: *\nAllow: /\n", "text/plain; charset=utf-8", cache="public, max-age=3600")
            return self._json(404, {"error": "not found"})

        do_HEAD = do_GET

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path != "/api/scan":
                self.close_connection = True
                return self._json(403, {"error": READ_ONLY_MSG})
            try:
                length = int(self.headers.get("Content-Length") or -1)
            except ValueError:
                length = -1
            if length < 0:
                self.close_connection = True
                return self._json(411, {"error": "Content-Length required"})
            if length > max_body:
                self.close_connection = True
                return self._json(413, {"error": "Text too long for the demo (max %d characters)." % max_text})
            raw = self.rfile.read(length)
            if not limiter.allow(self._client()):
                return self._json(429, {"error": "Too many checks — please wait a minute and try again."},
                                  extra={"Retry-After": "60"})
            try:
                req = json.loads(raw or b"{}")
                text = str(req.get("text", ""))[:max_text]
            except (ValueError, AttributeError):
                return self._json(400, {"error": "Send JSON: {\"text\": \"...\"}"})
            masked, counts = preview(text, detect(text, list(PACKS)))
            return self._json(200, {"masked": masked, "counts": counts, "session_counts": counts, "stored": False})

        def _refuse(self):
            self.close_connection = True
            self._json(405, {"error": READ_ONLY_MSG}, extra={"Allow": "GET, HEAD, POST"})

    for m in WRITE_METHODS:
        setattr(Handler, "do_" + m, Handler._refuse)
    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--site", default=os.environ.get("DEMO_SITE", os.path.join(HERE, "site")))
    ap.add_argument("--config", default=os.environ.get("DEMO_CONFIG", os.path.join(HERE, "config.json")))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 0)) or None)
    a = ap.parse_args(argv)
    with open(a.config, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("read_only"):
        sys.exit("config: read_only must be true for the public demo")
    with open(os.path.join(a.site, "index.html"), "rb") as f:
        page = f.read()
    port = a.port or cfg["port"]
    httpd = Server((os.environ.get("BIND", "0.0.0.0"), port),
                   make_handler(cfg, page, Limiter(cfg["rate_limit"])))
    sys.stderr.write("niyantrak-demo %s listening on :%d (read-only)\n" % (NIYANTRAK_VERSION, port))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
