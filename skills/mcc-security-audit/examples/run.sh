#!/usr/bin/env bash
# Offline, deterministic example. Run from the skill directory:
#   bash examples/run.sh
# Uses recorded rows (examples/fixture_rows.json); needs only python3, no
# credentials, no network, no third-party packages.
set -euo pipefail
python3 scripts/mcc_security_audit.py \
  --fixture examples/fixture_rows.json \
  --clients-dir examples/clients \
  --allowlist examples/allowlist.json \
  --hostile-list examples/hostile.json \
  --recent-days 30 \
  --max-admins 3
