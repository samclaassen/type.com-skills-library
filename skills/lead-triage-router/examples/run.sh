#!/usr/bin/env bash
# Run from the skill directory: bash examples/run.sh
set -euo pipefail

python3 scripts/lead_triage.py \
  --leads examples/sample_leads.csv \
  --routing examples/sample_routing.csv \
  --config config/rubric.json \
  --as-of 2026-09-22
