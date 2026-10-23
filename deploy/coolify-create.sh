#!/usr/bin/env bash
# Create the Niyantrak demo as its OWN Coolify project + application, then deploy it. Operator-run.
#
#   COOLIFY_BASE_URL=http://localhost:8000 COOLIFY_TOKEN=<write-capable token> deploy/coolify-create.sh            # report
#   COOLIFY_BASE_URL=http://localhost:8000 COOLIFY_TOKEN=<write-capable token> deploy/coolify-create.sh --apply    # create + deploy
#
# Touches nothing else: it reads the list of projects/applications, refuses if any OTHER application already
# claims demo.vigyanbytes.com, creates project "niyantrak-demo" (if missing) and application "niyantrak-demo"
# (if missing) from the public repo, and deploys only that application (by its uuid). Idempotent: a second run
# reports what exists and stops. Never restarts, edits or redeploys any existing resource. Never prints the token.
set -euo pipefail

MODE=report; [ "${1:-}" = "--apply" ] && MODE=apply
: "${COOLIFY_BASE_URL:?set COOLIFY_BASE_URL (e.g. http://localhost:8000 through vigyanbytes-web/scripts/coolify-tunnel.sh)}"
: "${COOLIFY_TOKEN:?set COOLIFY_TOKEN (a write-capable Coolify API token; the MCP token is read-only)}"
API="${COOLIFY_BASE_URL%/}/api/v1"
NAME=niyantrak-demo
DOMAIN=demo.vigyanbytes.com
REPO=https://github.com/vigyanbytes/niyantrak-demo
SERVER_UUID="${SERVER_UUID:-tdy7h3lhdmar60momx2gdicd}"          # "localhost" = vigyan-hostinger (read 2026-10-23)
DESTINATION_UUID="${DESTINATION_UUID:-wetxqmxmfvaa8nnhkzv86acc}" # network "coolify", same as vigyanbytes-web
RUN_OPTS="--read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m --cap-drop ALL --security-opt no-new-privileges --pids-limit 128"

log() { echo "[coolify-create:$MODE] $*"; }
call() { # method path [json]
  local out code
  out="$(curl -sS --max-time 60 -X "$1" -H "Authorization: Bearer $COOLIFY_TOKEN" -H 'Content-Type: application/json' \
         -w '\n%{http_code}' "$API$2" ${3:+--data "$3"})"
  code="${out##*$'\n'}"; out="${out%$'\n'*}"
  if [ "${code:0:1}" != 2 ]; then echo "HTTP $code from $1 $2: $out" >&2; return 1; fi
  printf '%s' "$out"
}
pyq() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

apps="$(call GET /applications)"
claim="$(pyq "a=d if isinstance(d,list) else d.get('data',d); print(' '.join(x['name']+':'+x['uuid'] for x in a if '$DOMAIN' in (x.get('fqdn') or '')))" <<<"$apps")"
mine="$(pyq "a=d if isinstance(d,list) else d.get('data',d); print(next((x['uuid'] for x in a if x.get('name')=='$NAME'),''))" <<<"$apps")"
if [ -n "$claim" ] && [ -z "$mine" ]; then log "STOP: $DOMAIN is already claimed by: $claim"; exit 1; fi
if [ -n "$mine" ]; then log "application $NAME exists ($mine) — nothing to create. Deploy it with: --deploy-only is not needed; use the Coolify UI or the MCP deploy tool."; exit 0; fi

proj="$(call GET /projects | pyq "print(next((p['uuid'] for p in d if p.get('name')=='$NAME'),''))")"
log "project $NAME: ${proj:-missing}; application $NAME: missing"
log "plan: create application from $REPO (main, Dockerfile, port 8080) at https://$DOMAIN, run options: $RUN_OPTS, memory 256m"
[ "$MODE" = apply ] || { log "report only; re-run with --apply"; exit 0; }

if [ -z "$proj" ]; then
  proj="$(call POST /projects "{\"name\":\"$NAME\",\"description\":\"Public read-only Niyantrak demo ($DOMAIN) from $REPO\"}" | pyq "print(d['uuid'])")"
  log "created project $proj"
fi
body="$(python3 - "$proj" <<EOF
import json, sys
print(json.dumps({"project_uuid": sys.argv[1], "server_uuid": "$SERVER_UUID", "destination_uuid": "$DESTINATION_UUID",
  "environment_name": "production", "git_repository": "$REPO", "git_branch": "main", "build_pack": "dockerfile",
  "ports_exposes": "8080", "name": "$NAME", "description": "Read-only public demo, fictional data, no secrets",
  "domains": "https://$DOMAIN", "instant_deploy": False}))
EOF
)"
app="$(call POST /applications/public "$body" | pyq "print(d['uuid'])")"
log "created application $app"
call PATCH "/applications/$app" "{\"custom_docker_run_options\":\"$RUN_OPTS\",\"limits_memory\":\"256m\",\"health_check_enabled\":false}" >/dev/null \
  && log "set run options + memory limit (container health = the image's HEALTHCHECK on /healthz)"
dep="$(call POST "/deploy?uuid=$app&force=false" || call GET "/deploy?uuid=$app&force=false")"   # 4.2+ POST, older GET
log "deploy queued: $(pyq "print(json.dumps(d)[:300])" <<<"$dep")"
log "next: add DNS (deploy/cloudflare-demo-dns.sh), then scripts/smoke.sh https://$DOMAIN"
