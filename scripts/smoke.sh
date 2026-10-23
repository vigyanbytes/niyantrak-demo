#!/usr/bin/env bash
# Smoke test for the Niyantrak public demo.
#
#   scripts/smoke.sh [BASE_URL] [-- extra curl args]
#   scripts/smoke.sh http://127.0.0.1:8080
#   scripts/smoke.sh https://demo.vigyanbytes.com
#   scripts/smoke.sh https://demo.vigyanbytes.com -- --resolve demo.vigyanbytes.com:443:<origin-ip> -k
#
# Checks: health 200, page 200 + HTML + security headers, Try-it scan returns placeholders (and not the raw
# value), scan responses are no-store, write endpoints refused (403/405), oversized body refused (413).
# RATE_LIMIT=1 also checks that a burst of scans gets 429 (run it last: it uses up your client's allowance).
set -u
BASE="${1:-http://127.0.0.1:8080}"; shift || true
[ "${1:-}" = "--" ] && shift
EXTRA=("$@")
pass=0; fail=0
ok()  { echo "PASS  $*"; pass=$((pass + 1)); }
bad() { echo "FAIL  $*"; fail=$((fail + 1)); }
c()   { curl -sS --max-time 15 "${EXTRA[@]}" "$@"; }
code() { c -o /dev/null -w '%{http_code}' "$@"; }
expect() { local want="$1" label="$2"; shift 2; local got; got="$(code "$@")"
  [ "$got" = "$want" ] && ok "$label -> $got" || bad "$label -> $got (want $want)"; }

# health
h="$(c "$BASE/healthz")"; [ "$(code "$BASE/healthz")" = 200 ] && grep -q '"ok": true' <<<"$h" \
  && ok "GET /healthz 200 $h" || bad "GET /healthz: $h"

# page + headers
hdr="$(c -D - -o /tmp/.nyk-demo-page.$$ "$BASE/")"
grep -qi '^HTTP/[0-9.]* 200' <<<"$hdr" && ok "GET / 200" || bad "GET / status: $(head -1 <<<"$hdr")"
grep -qi '^content-type: text/html' <<<"$hdr" && ok "GET / is text/html" || bad "GET / content-type"
grep -q 'Sample data' /tmp/.nyk-demo-page.$$ && grep -q 'Public demo' /tmp/.nyk-demo-page.$$ \
  && ok "page is the demo, labelled Sample data" || bad "page content"
rm -f /tmp/.nyk-demo-page.$$
for hname in content-security-policy x-frame-options x-content-type-options referrer-policy strict-transport-security; do
  grep -qi "^$hname:" <<<"$hdr" && ok "header $hname" || bad "missing header $hname"
done
grep -qi "^content-security-policy:.*frame-ancestors 'none'" <<<"$hdr" && ok "CSP frame-ancestors 'none'" || bad "CSP frame-ancestors"

# try it
AADHAAR='2345 6789 0124'
body="{\"text\":\"Hi, my Aadhaar is $AADHAAR, PAN ABCPE1234F, mail ramesh@example.com, card 4111 1111 1111 1111\"}"
shdr="$(c -D - -o /tmp/.nyk-demo-scan.$$ -X POST -H 'Content-Type: application/json' --data "$body" "$BASE/api/scan")"
r="$(cat /tmp/.nyk-demo-scan.$$)"; rm -f /tmp/.nyk-demo-scan.$$
grep -q '<AADHAAR_1>' <<<"$r" && grep -q '<PAN_1>' <<<"$r" && grep -q '<EMAIL_1>' <<<"$r" && grep -q '<CARD_1>' <<<"$r" \
  && ok "POST /api/scan returns placeholders" || bad "POST /api/scan: $r"
grep -q "$AADHAAR" <<<"$r" && bad "scan response still contains the raw Aadhaar" || ok "raw value not echoed"
grep -qi '^cache-control: no-store' <<<"$shdr" && ok "scan is Cache-Control: no-store" || bad "scan cache-control"

# writes refused
expect 403 "POST /api/join"                       -X POST -H 'Content-Type: application/json' --data '{}' "$BASE/api/join"
expect 403 "POST /api/join/token"                 -X POST --data '{}' "$BASE/api/join/token"
expect 403 "POST /api/nodes/nyk-0/approve"        -X POST --data '{}' "$BASE/api/nodes/nyk-0/approve"
expect 403 "POST /api/docs/regenerate"            -X POST --data '{}' "$BASE/api/docs/regenerate"
expect 403 "POST /api/discover"                   -X POST --data '{}' "$BASE/api/discover"
expect 405 "PUT /"                                -X PUT --data x "$BASE/"
expect 405 "DELETE /api/scan"                     -X DELETE "$BASE/api/scan"
expect 405 "PATCH /api/nodes"                     -X PATCH --data x "$BASE/api/nodes"
expect 404 "GET /api/nodes (no live API)"         "$BASE/api/nodes"
big="$(head -c 70000 /dev/zero | tr '\0' a)"
expect 413 "POST /api/scan 70 KB body"            -X POST -H 'Content-Type: application/json' --data "{\"text\":\"$big\"}" "$BASE/api/scan"

if [ "${RATE_LIMIT:-0}" = 1 ]; then
  seen=""
  for _ in $(seq 1 25); do
    seen="$seen $(code -X POST -H 'Content-Type: application/json' --data '{"text":"x"}' "$BASE/api/scan")"
  done
  grep -q 429 <<<"$seen" && ok "rate limit: burst gets 429" || bad "rate limit never hit:$seen"
fi

echo "---- $pass passed, $fail failed"
[ "$fail" = 0 ]
