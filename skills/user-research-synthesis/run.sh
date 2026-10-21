#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data and writes nothing. PYTHON=python3.8 bash run.sh uses another
# interpreter; the output must be identical.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
PY="${PYTHON:-python3}"

echo "=== 1. Research synthesis report (markdown, for a Type doc) ==="
"$PY" build_synthesis.py --bundle sample_bundle.json --config synthesis.json --as-of 2026-10-02

echo
echo "=== 2. Insight cards for a research repository (CSV; patterns only) ==="
"$PY" build_synthesis.py --bundle sample_bundle.json --config synthesis.json --as-of 2026-10-02 --format csv
