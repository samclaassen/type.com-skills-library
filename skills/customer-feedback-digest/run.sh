#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data. The state file is copied to a
# temp file so the sample state is never modified and the output is repeatable.
set -euo pipefail

STATE="$(mktemp)"
trap 'rm -f "$STATE"' EXIT
cp sample_state.json "$STATE"
BASE=(--bundle sample_bundle.json --config digest.json --internal-domain tamsquill.example)

echo "=== 1. Scheduled Monday run: digest for last week (Type doc) ==="
python3 build_digest.py "${BASE[@]}" \
  --as-of 2026-10-05T08:00:00-07:00 \
  --scheduled --state "$STATE"

echo
echo "=== 2. Slack summary for the configured channel (reads state, never writes it) ==="
python3 build_digest.py "${BASE[@]}" \
  --as-of 2026-10-05T08:00:00-07:00 \
  --state "$STATE" --format slack --doc-url "https://type.com/sample-workspace/docs/sample-digest-2026-w40"

echo
echo "=== 3. After the Slack post succeeds: confirm it ==="
python3 build_digest.py --state "$STATE" --confirm-posted 2026-W40 \
  --as-of 2026-10-05T08:01:00-07:00 --post-ref "https://slack.example/archives/C0SAMPLE/p3"

echo
echo "=== 4. A second scheduled run for the same week posts nothing ==="
python3 build_digest.py "${BASE[@]}" \
  --as-of 2026-10-05T09:00:00-07:00 \
  --scheduled --state "$STATE"
