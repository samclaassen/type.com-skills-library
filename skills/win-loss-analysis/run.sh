#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled, fictional sample data. Nothing is written or posted.
set -euo pipefail

echo "=== 1. Q3 win/loss report from the CRM deal list and Granola calls ==="
python3 build_winloss.py \
  --bundle sample_bundle.json \
  --as-of 2026-10-05T09:00:00-07:00 \
  --internal-domain tamsquill.example

echo
echo "=== 2. Slack summary draft of the same report (shown for approval, never posted by the script) ==="
python3 build_winloss.py \
  --bundle sample_bundle.json \
  --as-of 2026-10-05T09:00:00-07:00 \
  --internal-domain tamsquill.example \
  --format slack \
  --doc-url https://type.com/sample-win-loss-report

echo
echo "=== 3. Deal list from a CSV export, amount-weighted, 4 deals (counts only, no percentages) ==="
python3 build_winloss.py \
  --bundle sample_bundle_csv.json \
  --deals-csv sample_deals.csv \
  --as-of 2026-10-01T09:00:00-04:00 \
  --internal-domain tamsquill.example \
  --weighting amount
