#!/usr/bin/env bash
# Offline, deterministic demo of scripts/check_voice_rules.py.
# Run from the skill directory: bash examples/run.sh
#
# The same synthetic draft is checked against two synthetic clients only to
# show that rules are scoped per client. In real use, a draft is checked only
# against the client it was written for.
set -u
export LC_ALL=C PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")/.." || exit 2
CHECK="python3 scripts/check_voice_rules.py --clients-dir examples/clients"

echo "== list clients with guidelines =="
$CHECK --list
echo "exit: $?"

for client in harbor-and-vine northwind-ledger; do
  echo
  echo "== check examples/draft.txt for $client =="
  $CHECK --client "$client" --draft examples/draft.txt
  echo "exit: $?"
done
exit 0
