#!/usr/bin/env bash
# Offline, deterministic example. Run from the skill directory:
#   bash examples/run.sh
# Uses recorded (synthetic) change_event/change_status rows; makes no network calls
# and needs no Google Ads credentials or third-party packages.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "### 1. One client, 90-day window"
python3 scripts/change_history.py \
  --client harbor-and-vine --days 90 \
  --clients-dir examples/clients \
  --fixture examples/fixture_change_events.json

echo
echo "### 2. Portfolio view, 14-day window"
python3 scripts/change_history.py \
  --all-clients --days 14 \
  --clients-dir examples/clients \
  --fixture examples/fixture_change_events.json
