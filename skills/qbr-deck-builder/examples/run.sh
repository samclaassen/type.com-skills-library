#!/usr/bin/env bash
# Run from the skill directory: bash examples/run.sh
set -euo pipefail

python3 scripts/qbr_builder.py \
  --account-id ACC-1001 \
  --usage examples/sample_usage_monthly.csv \
  --goals examples/sample_goals.csv \
  --support examples/sample_support.csv \
  --commercial examples/sample_commercial.csv \
  --products examples/sample_products.csv \
  --config config/qbr.json \
  --as-of 2026-09-22
