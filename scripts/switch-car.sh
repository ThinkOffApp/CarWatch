#!/usr/bin/env bash
# Switch this Pi's car identity: scripts/switch-car.sh eclass|gle
#
# Merges profiles/<name>.json into ~/.carwatch/config.json (handle + car
# block only), optionally installs a privately staged API key, restarts the
# services. Wifi is provisioned separately via the dashboard /api/wifi so
# credentials never touch the repo or the room.
#
#   1. (on the staging machine) place the new agent's key in
#      ~/.carwatch/staged-api-key (one line) - or skip to keep the old key
#   2. bash scripts/switch-car.sh eclass
set -euo pipefail

PROFILE="${1:?usage: switch-car.sh <profile: eclass|gle>}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE_FILE="$DIR/profiles/$PROFILE.json"
CONFIG="$(PYTHONPATH="$DIR" python3 -m carwatch.config path)"
STAGED_KEY="$HOME/.carwatch/staged-api-key"

[ -f "$PROFILE_FILE" ] || { echo "no such profile: $PROFILE_FILE"; exit 1; }
[ -f "$CONFIG" ] || { echo "no config at $CONFIG"; exit 1; }

python3 - "$PROFILE_FILE" "$CONFIG" "$STAGED_KEY" <<'EOF'
import json, os, sys
profile_file, config_file, staged_key = sys.argv[1:4]
profile = json.load(open(profile_file))
cfg = json.load(open(config_file))
cfg["handle"] = profile["handle"]
cfg["car"] = profile["car"]
if os.path.exists(staged_key):
    cfg["api_key"] = open(staged_key).read().strip()
    os.remove(staged_key)
    print("installed staged api key (staging file removed)")
tmp = config_file + ".tmp"
json.dump(cfg, open(tmp, "w"), indent=2)
os.replace(tmp, config_file)
print(f"config now {profile['handle']}: {profile['car']['identity']}")
EOF

if [ "${SWITCH_NO_RESTART:-}" = "1" ]; then
  echo "skipping service restart (caller handles it)"
else
  echo "restarting services..."
  sudo systemctl restart carwatch-agent carwatch-chat carwatch-presence 2>/dev/null || true
fi
echo "DONE - this Pi now speaks as $(python3 -c "import json;print(json.load(open('$CONFIG'))['handle'])")"
