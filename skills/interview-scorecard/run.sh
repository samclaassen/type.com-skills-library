#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data. The scorecard log is copied to a
# temp file so the sample log is never modified and the output is repeatable.
# Set PYTHON=python3.8 (or similar) to try another interpreter.
set -euo pipefail

PY="${PYTHON:-python3}"
LOG="$(mktemp)"
trap 'rm -f "$LOG" "$LOG.lock"' EXIT
cp sample_scorecard_log.md "$LOG"

echo "=== 1. Check the role's rubric before scoring anything ==="
"$PY" build_scorecard.py --check-rubric --rubric sample_rubric_finance_ops_analyst.json

echo
echo "=== 2. Scheduled run after the hiring-manager interview: draft scorecard (logged as pending) ==="
"$PY" build_scorecard.py \
  --rubric sample_rubric_finance_ops_analyst.json \
  --bundle sample_bundle_single.json \
  --as-of 2026-09-29 \
  --scheduled --log "$LOG"

echo
echo "=== 3. The draft reached the interviewer privately: confirm delivery ==="
"$PY" build_scorecard.py --log "$LOG" --confirm-delivered single:finance-ops-analyst:sample-hm-0142

echo
echo "=== 4. Next scheduled run: the same interview was already delivered ==="
"$PY" build_scorecard.py \
  --rubric sample_rubric_finance_ops_analyst.json \
  --bundle sample_bundle_single.json \
  --as-of 2026-09-30 \
  --scheduled --log "$LOG"

echo
echo "=== 5. Panel debrief: all three interviewers have filed their scores ==="
"$PY" build_scorecard.py \
  --rubric sample_rubric_finance_ops_analyst.json \
  --bundle sample_bundle_panel.json \
  --as-of 2026-10-02
