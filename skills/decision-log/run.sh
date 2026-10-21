#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data. The decision log lives in a temp
# directory, so nothing in this folder is modified and the output is repeatable.
# The script's messages (stderr) are shown inline so the two-phase flow is visible.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LOG="$TMP/decision-log.json"

echo "=== 1. Weekly scheduled run, Fri Oct 9 (first run, empty log): ask for the window, then stage the report ==="
python3 build_decision_log.py --next-window --as-of 2026-10-09T16:00:00-07:00 --log "$LOG" 2>&1
python3 build_decision_log.py --bundle sample_bundle_week1.json --as-of 2026-10-09T16:00:00-07:00 \
  --log "$LOG" --scheduled 2>&1

echo
echo "=== 2. The schedule fires again before the post is confirmed: stops, posts nothing ==="
python3 build_decision_log.py --bundle sample_bundle_week1.json --as-of 2026-10-09T16:05:00-07:00 \
  --log "$LOG" --scheduled 2>&1 || echo "(exit $?)"

echo
echo "=== 3. The agent posted the report, then confirms it ==="
python3 build_decision_log.py --confirm-posted weekly:2026-10-05..2026-10-09 --as-of 2026-10-09T16:01:00-07:00 \
  --log "$LOG" 2>&1

echo
echo "=== 4. The schedule fires again at 4:30pm: --next-window flags this week as already posted; the run posts nothing ==="
python3 build_decision_log.py --next-window --as-of 2026-10-09T16:30:00-07:00 --log "$LOG" 2>&1
# The bundle for that window (Fri Oct 9 only; no meetings since the 4pm report), as the agent would build it:
cat >"$TMP/bundle_fri_1630.json" <<'JSON'
{"schema_version": 1,
 "scan": {"from": "2026-10-09", "to": "2026-10-09", "cadence": "weekly"},
 "sources": {"meetings": {"provider": "granola", "status": "ok", "detail": null, "plan": "business"}},
 "meetings": [], "decisions": [], "user_actions": []}
JSON
python3 build_decision_log.py --bundle "$TMP/bundle_fri_1630.json" --as-of 2026-10-09T16:30:00-07:00 \
  --log "$LOG" --scheduled 2>&1

echo
echo "=== 5. Next weekly run, Fri Oct 16: the window starts where the last posted report ended ==="
python3 build_decision_log.py --next-window --as-of 2026-10-16T16:00:00-07:00 --log "$LOG" 2>&1

echo
echo "=== 6. Fri Oct 16 report: a reversal, a possible conflict, a missing owner and a passed revisit date ==="
python3 build_decision_log.py --bundle sample_bundle_week2.json --as-of 2026-10-16T16:00:00-07:00 \
  --log "$LOG" --scheduled 2>&1
python3 build_decision_log.py --confirm-posted weekly:2026-10-09..2026-10-16 --as-of 2026-10-16T16:01:00-07:00 \
  --log "$LOG" 2>&1

echo
echo "=== 7. Search: \"when did we decide the annual discount?\" ==="
python3 build_decision_log.py --search "when did we decide the annual discount?" --as-of 2026-10-16T16:05:00-07:00 \
  --log "$LOG" 2>&1

echo
echo "=== 8. The full log, for the Type doc or markdown file you keep ==="
python3 build_decision_log.py --render-log --as-of 2026-10-16T16:05:00-07:00 --log "$LOG" 2>&1
