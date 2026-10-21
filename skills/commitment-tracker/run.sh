#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data. The state file lives in a temp
# directory, so nothing in this folder is modified and the output is repeatable.
# The script's messages (stderr) are shown inline so the two-phase flow is visible.
set -euo pipefail

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
STATE="$TMP/commitment-state.json"
COMMON=(--config commitments.json --internal-domain brackenvole.example --state "$STATE")

echo "=== 1. Weekly scheduled run, Fri Oct 9 (first run, empty state): digest staged as pending ==="
python3 build_commitments.py --bundle sample_bundle_week1.json --as-of 2026-10-09T16:00:00-07:00 \
  "${COMMON[@]}" --scheduled 2>&1

echo
echo "=== 2. The schedule fires again before the post is confirmed: stops, posts nothing ==="
python3 build_commitments.py --bundle sample_bundle_week1.json --as-of 2026-10-09T16:05:00-07:00 \
  "${COMMON[@]}" --scheduled 2>&1 || echo "(exit $?)"

echo
echo "=== 3. The agent posted the digest, then confirms it ==="
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --as-of 2026-10-09T16:01:00-07:00 \
  --state "$STATE" 2>&1

echo
echo "=== 4. The schedule fires again at 4:30pm: --next-window flags this week as already posted; the run posts nothing ==="
python3 build_commitments.py --next-window weekly --as-of 2026-10-09T16:30:00-07:00 --state "$STATE" 2>&1
# The bundle for that window (Fri Oct 9 only; no meetings since the 4pm digest), as the agent would build it:
cat >"$TMP/bundle_fri_1630.json" <<'JSON'
{"schema_version": 1,
 "scan": {"from": "2026-10-09", "to": "2026-10-09", "cadence": "weekly"},
 "sources": {"meetings": {"provider": "granola", "status": "ok", "detail": null, "plan": "free"},
             "email": {"provider": "gmail", "status": "ok", "detail": null},
             "slack": {"provider": "slack", "status": "ok", "detail": null},
             "tracker": {"provider": null, "status": "not_configured", "detail": null},
             "calendar": {"provider": "google", "status": "ok", "detail": null}},
 "meetings": [], "commitments": [], "evidence": [], "user_actions": []}
JSON
python3 build_commitments.py --bundle "$TMP/bundle_fri_1630.json" --as-of 2026-10-09T16:30:00-07:00 \
  "${COMMON[@]}" --scheduled 2>&1

echo
echo "=== 5. Next weekly run, Fri Oct 16: window starts where the last posted run ended ==="
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state "$STATE" 2>&1
python3 build_commitments.py --bundle sample_bundle_week2.json --as-of 2026-10-16T16:00:00-07:00 \
  "${COMMON[@]}" --scheduled 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-09..2026-10-16 --as-of 2026-10-16T16:01:00-07:00 \
  --state "$STATE" 2>&1
