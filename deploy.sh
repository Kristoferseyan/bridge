#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
: "${DEPLOY_HOST:?set DEPLOY_HOST to the ssh host to deploy to}"
[ -z "$(git status --porcelain)" ] || echo "note: uncommitted changes are NOT deployed (deploying HEAD $(git rev-parse --short HEAD))"
git archive HEAD | ssh "$DEPLOY_HOST" 'mkdir -p ~/bridge && tar -x -C ~/bridge'
[ ! -f accounts.json ] || ssh "$DEPLOY_HOST" 'umask 077 && cat > ~/bridge/accounts.json' < accounts.json
ssh "$DEPLOY_HOST" "BRIDGE_NTFY_TOPIC=$(printf %q "${BRIDGE_NTFY_TOPIC:-}") bash -s" <<'REMOTE'
set -euo pipefail
cd ~/bridge
if [ ! -f .env ]; then
  login=$(tailscale status --json | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['User'][str(d['Self']['UserID'])]['LoginName'])")
  host=$(tailscale status --json | python3 -c "import sys,json;print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")
  umask 077
  printf 'BRIDGE_ALLOWED_LOGINS=%s\nBRIDGE_NTFY_TOPIC=%s\nBRIDGE_URL=https://%s/bridge/\n' "$login" "$BRIDGE_NTFY_TOPIC" "$host" > .env
fi
mkdir -p ~/.config/systemd/user
install -m 644 bridge.service ~/.config/systemd/user/bridge.service
systemctl --user daemon-reload
systemctl --user enable --now bridge >/dev/null 2>&1
systemctl --user restart bridge
sleep 1
systemctl --user is-active bridge
REMOTE
