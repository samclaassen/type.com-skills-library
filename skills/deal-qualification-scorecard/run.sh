#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data. Nothing is written anywhere; the script only prints.
set -euo pipefail

BASE=(--as-of 2026-10-12T09:00:00-07:00 --internal-domain talvessa.example)

echo "=== 1. \"Qualify the Ostrevane deal\": MEDDICC scorecard ==="
python3 build_scorecard.py --bundle sample_bundle_ostrevane.json --framework framework_meddicc.json "${BASE[@]}"

echo
echo "=== 2. CRM field-update draft for the same deal (not written to the CRM) ==="
python3 build_scorecard.py --bundle sample_bundle_ostrevane.json --framework framework_meddicc.json "${BASE[@]}" \
  --format crm-draft

echo
echo "=== 3. Batch: three open deals in one table ==="
python3 build_scorecard.py --framework framework_meddicc.json "${BASE[@]}" \
  --bundle sample_bundle_ostrevane.json --bundle sample_bundle_ketterbrae.json --bundle sample_bundle_dunnolark.json

echo
echo "=== 4. One deal whose meeting notes could not be read: nothing is scored (exit 3) ==="
python3 build_scorecard.py --bundle sample_bundle_dunnolark.json --framework framework_meddicc.json "${BASE[@]}" 2>&1 \
  || echo "(exit code $?)"
