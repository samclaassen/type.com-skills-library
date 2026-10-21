#!/usr/bin/env bash
# Run from the skill directory: bash examples/run.sh
set -euo pipefail

python3 scripts/rfp_builder.py \
  --requirements examples/sample_requirements.csv \
  --answers examples/sample_answer_library.csv \
  --config config/matching.json \
  --as-of 2026-09-22 \
  --infer-classification
