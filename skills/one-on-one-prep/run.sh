#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data. Nothing is written anywhere; the script keeps no state.
set -euo pipefail
PY="${PYTHON:-python3}"

echo "=== 1. Manager preparing: Dana's 1:1 with Riley (Granola + Linear + Slack) ==="
"$PY" -B build_prep.py --bundle sample_bundle_manager.json --as-of 2026-10-13T09:30:00-07:00

echo
echo "=== 2. The same 1:1 from the other side: Riley (the report) preparing, notes only ==="
"$PY" -B build_prep.py --bundle sample_bundle_report.json --as-of 2026-10-13T09:30:00-07:00

echo
echo "=== 3. First 1:1 with a new report: no history yet ==="
"$PY" -B build_prep.py --bundle sample_bundle_first.json --as-of 2026-10-13T09:30:00-07:00
