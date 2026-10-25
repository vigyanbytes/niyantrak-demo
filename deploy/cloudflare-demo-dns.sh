#!/usr/bin/env bash
# Add ONE Cloudflare record: demo.vigyanbytes.com A <origin> (proxied). Operator-run.
#
#   CLOUDFLARE_API_TOKEN=<Zone:DNS:Edit for vigyanbytes.com> deploy/cloudflare-demo-dns.sh            # report
#   CLOUDFLARE_API_TOKEN=<...>                                deploy/cloudflare-demo-dns.sh --apply    # create
#
# Desired state lives in Vigyan-Virtual-Cloud iac/opentofu/sites/vigyanbytes.json (dns_records); this script only
# bridges the gap until OpenTofu is adopted there. It reads the zone's records and creates this one record if it
# is missing. It never edits or deletes any other record, redirect rule or cache rule. Never prints the token.
set -euo pipefail
MODE=report; [ "${1:-}" = "--apply" ] && MODE=apply
: "${CLOUDFLARE_API_TOKEN:?set CLOUDFLARE_API_TOKEN (Zone:DNS:Edit for vigyanbytes.com)}"
ZONE_ID="${CF_ZONE_ID:?set CF_ZONE_ID (the vigyanbytes.com zone id, from the private infra config)}"
ORIGIN_IP="${ORIGIN_IP:?set ORIGIN_IP (the origin server address, from the private infra config)}"
NAME=demo.vigyanbytes.com
API=https://api.cloudflare.com/client/v4
H=(-H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json")
log() { echo "[cloudflare-demo-dns:$MODE] $*"; }

live="$(curl -sS --max-time 30 "${H[@]}" "$API/zones/$ZONE_ID/dns_records?name=$NAME")"
n="$(python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('success'), d.get('errors'); r=d['result']; print(len(r)); [print(' ', x['type'], x['content'], 'proxied=%s' % x['proxied'], file=sys.stderr) for x in r]" <<<"$live")"
if [ "$n" != 0 ]; then log "$NAME already has $n record(s) (listed above) — nothing to do"; exit 0; fi
log "plan: create $NAME A $ORIGIN_IP proxied=true"
[ "$MODE" = apply ] || { log "report only; re-run with --apply"; exit 0; }
out="$(curl -sS --max-time 30 -X POST "${H[@]}" "$API/zones/$ZONE_ID/dns_records" --data \
  "{\"type\":\"A\",\"name\":\"$NAME\",\"content\":\"$ORIGIN_IP\",\"proxied\":true,\"ttl\":1,\"comment\":\"Niyantrak public demo (Coolify app niyantrak-demo)\"}")"
python3 -c "import json,sys; d=json.load(sys.stdin); print('created' if d.get('success') else 'FAILED', d.get('errors') or d['result']['id'])" <<<"$out"
