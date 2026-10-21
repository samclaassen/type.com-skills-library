#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data. The handoff log is copied to a
# temp file so the sample log is never modified and the output is repeatable.
set -euo pipefail

LOG="$(mktemp)"
trap 'rm -f "$LOG" "$LOG.lock"' EXIT
cp sample_handoff_log.md "$LOG"

echo "=== 1. Deal closed-won: scheduled run writes the handoff doc (logged as pending) ==="
python3 build_handoff.py \
  --bundle sample_bundle_quillfen.json \
  --config handoff.json \
  --as-of 2026-10-01T09:00:00-07:00 \
  --internal-domain corvalto.example \
  --scheduled --log "$LOG"

echo
echo "=== 2. Slack post draft for the CS channel (not posted) ==="
python3 build_handoff.py \
  --bundle sample_bundle_quillfen.json \
  --config handoff.json \
  --as-of 2026-10-01T09:00:00-07:00 \
  --internal-domain corvalto.example \
  --format slack --post-to "#cs-handoffs" \
  --doc-url "https://type.com/sample/quillfen-handoff"

echo
echo "=== 3. Doc saved and post sent: the agent confirms delivery ==="
python3 build_handoff.py \
  --confirm-delivered hubspot:deal/48213 \
  --as-of 2026-10-01T09:05:00-07:00 \
  --log "$LOG"

echo
echo "=== 4. Next scheduled run: this deal is already handed off ==="
python3 build_handoff.py \
  --bundle sample_bundle_quillfen.json \
  --config handoff.json \
  --as-of 2026-10-02T09:00:00-07:00 \
  --internal-domain corvalto.example \
  --scheduled --log "$LOG"
