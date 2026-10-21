#!/usr/bin/env bash
# Regenerate examples/expected_output.txt with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/audit_account.py \
  --campaigns examples/data/campaigns.csv \
  --ads examples/data/ads.csv \
  --keywords examples/data/keywords.csv \
  --search-terms examples/data/search_terms.csv \
  --account "Aurora Bikeworks — Google Ads (US/CA)" \
  --period-start 2026-09-01 \
  --as-of 2026-09-21
