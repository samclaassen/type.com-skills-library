#!/usr/bin/env bash
# Run from the skill directory: bash examples/run.sh
set -euo pipefail

python3 scripts/pipeline_audit.py \
  --deals examples/sample_deals.csv \
  --stages examples/sample_stages.csv \
  --stage-history examples/sample_stage_history.csv \
  --config config/rules.json \
  --as-of 2026-09-22
