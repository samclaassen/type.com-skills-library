#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data. The briefing log is copied to a
# temp file so the sample log is never modified and the output is repeatable.
set -euo pipefail

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
cp sample_briefing_log.md "$LOG"

echo "=== 1. Scheduled run: external call starting in 25 minutes ==="
python3 build_briefing.py \
  --bundle sample_bundle_outfitters.json \
  --config briefing.json \
  --as-of 2026-10-06T09:35:00-07:00 \
  --internal-domain ledgerline.example \
  --enrichment basic \
  --scheduled --lookahead-minutes 30 \
  --log "$LOG"

echo
echo "=== 2. Next 15-minute run: same event is already in the log ==="
python3 build_briefing.py \
  --bundle sample_bundle_outfitters.json \
  --config briefing.json \
  --as-of 2026-10-06T09:50:00-07:00 \
  --internal-domain ledgerline.example \
  --scheduled --lookahead-minutes 30 \
  --log "$LOG"

echo
echo "=== 3. Internal-only meeting ==="
python3 build_briefing.py \
  --bundle sample_bundle_internal.json \
  --config briefing.json \
  --as-of 2026-10-06T09:35:00-07:00 \
  --internal-domain ledgerline.example \
  --scheduled --lookahead-minutes 30 \
  --log "$LOG"
