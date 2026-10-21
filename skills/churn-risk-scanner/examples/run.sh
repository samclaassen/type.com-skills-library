#!/usr/bin/env bash
# Run from the skill directory: bash examples/run.sh
set -euo pipefail

python3 scripts/churn_scanner.py \
  --accounts examples/sample_accounts.csv \
  --usage examples/sample_usage_monthly.csv \
  --support examples/sample_support.csv \
  --contacts examples/sample_contacts.csv \
  --invoices examples/sample_invoices.csv \
  --config config/risk_model.json \
  --as-of 2026-09-22
