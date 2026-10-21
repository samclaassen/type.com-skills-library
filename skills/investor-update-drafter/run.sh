#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data (sample_bundle.json, sample_metrics.csv). Nothing is sent anywhere.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

echo "=== 1. Investor draft for September 2026 (default: --audience investors --format draft) ==="
python3 build_update.py \
  --bundle sample_bundle.json \
  --metrics-csv sample_metrics.csv \
  --as-of 2026-10-02T09:00:00-04:00

echo
echo "=== 2. Board version, email body only (after the founder approved the draft) ==="
python3 build_update.py \
  --bundle sample_bundle.json \
  --metrics-csv sample_metrics.csv \
  --as-of 2026-10-02T09:00:00-04:00 \
  --audience board --format email
