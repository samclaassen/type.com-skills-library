#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Sample data is fictional (harborandvine.example); see "Sample data" in DATA_CONTRACT.md.
set -euo pipefail

python3 gsc_analyze.py \
  --current sample_current.csv \
  --prior sample_prior.csv \
  --devices sample_devices.csv \
  --countries sample_countries.csv \
  --totals sample_totals.csv \
  --config settings.json \
  --property sc-domain:harborandvine.example \
  --as-of 2026-09-28
