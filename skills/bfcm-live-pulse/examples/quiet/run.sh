#!/usr/bin/env bash
# Cyber Monday, 9:00 am ET, nothing wrong. Quiet mode (how the hourly automation runs it)
# prints nothing and exits 0; the same run without --quiet shows what a summary time
# would post. Output must match examples/quiet/expected_output.txt exactly. Regenerate it with:
#   bash examples/quiet/run.sh > examples/quiet/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/../.."

args=(--plan examples/data/plan.csv
      --hourly examples/quiet/data/hourly.csv
      --ly-hourly examples/quiet/data/ly_hourly.csv
      --spend examples/quiet/data/spend_today.csv
      --discounts examples/quiet/data/discount_usage.csv
      --inventory examples/quiet/data/inventory.csv
      --config examples/quiet/data/config.json)

out="$(python3 scripts/live_pulse.py "${args[@]}" --quiet)"
echo "--quiet: exit 0, printed ${#out} characters"
echo
echo "Without --quiet:"
echo
python3 scripts/live_pulse.py "${args[@]}"
