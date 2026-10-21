#!/usr/bin/env bash
# End-to-end checks for Ecommerce Bounce Guard. No network or credentials needed.
#   bash tests/run_tests.sh
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."
tmp="$(mktemp -d)"
trap 'kill "${mock_pid:-0}" 2>/dev/null || true; rm -rf "$tmp"' EXIT
pass=0
ok() { pass=$((pass + 1)); echo "ok $pass - $1"; }
fail() { echo "FAIL - $1"; exit 1; }

# 1. Sample output is byte-identical to the published expected output.
bash examples/run.sh > "$tmp/out.txt"
cmp -s "$tmp/out.txt" examples/expected_output.txt || { diff "$tmp/out.txt" examples/expected_output.txt | head; fail "sample output differs"; }
ok "sample run matches examples/expected_output.txt"

# 2. Live fetcher against a mock Klaviyo API, then analyze: same report as the sample.
port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
python3 tests/mock_klaviyo.py "$port" & mock_pid=$!
for _ in $(seq 50); do python3 -c "import socket; socket.create_connection(('127.0.0.1',$port),0.2)" 2>/dev/null && break; sleep 0.1; done
KLAVIYO_API_BASE="http://127.0.0.1:$port/api" KLAVIYO_REPORT_GAP_SECONDS=0 KLAVIYO_API_KEY=pk_test_mock \
  python3 scripts/klaviyo_fetch.py --account "Fernhill Goods" --as-of 2026-10-04 --out "$tmp/live.json" 2> "$tmp/fetch.log" \
  || { cat "$tmp/fetch.log"; fail "fetcher failed against mock"; }
grep -q "HTTP 429, retrying" "$tmp/fetch.log" || fail "fetcher did not retry the 429"
ok "fetcher pages flows, events and reports, skips archived and SMS rows, retries 429"
python3 scripts/bounce_audit.py --bundle "$tmp/live.json" --shopify-daily examples/data/shopify_daily.csv > "$tmp/live.txt"
cmp -s "$tmp/live.txt" examples/expected_output.txt || { diff "$tmp/live.txt" examples/expected_output.txt | head -20; fail "fetched bundle gives a different report"; }
ok "fetched bundle produces the identical report"

# 3. Wrong key fails clearly with exit 1 and a scope hint.
set +e
KLAVIYO_API_BASE="http://127.0.0.1:$port/api" KLAVIYO_API_KEY=pk_wrong \
  python3 scripts/klaviyo_fetch.py --account x --out "$tmp/x.json" 2> "$tmp/bad.log"; rc=$?
set -e
[ "$rc" = 1 ] && grep -q "HTTP 401" "$tmp/bad.log" && grep -q "flows:read" "$tmp/bad.log" || fail "bad key not reported (rc=$rc)"
ok "bad key exits 1 with a scope hint"

# 4. Missing key exits 2.
set +e; env -u KLAVIYO_API_KEY python3 scripts/klaviyo_fetch.py --account x --out "$tmp/x.json" 2>/dev/null; rc=$?; set -e
[ "$rc" = 2 ] || fail "missing key should exit 2 (got $rc)"
ok "missing key exits 2"

# 5. Healthy account: no alert, no pause advice.
python3 - "$tmp" <<'EOF'
import json, sys
b = json.load(open("examples/data/bundle.json"))
for s in ("weekly", "daily"):
    for r in b[s]["rows"]:
        r["bounced"] = [0] * len(r["bounced"])
b["bounces"] = []
json.dump(b, open(sys.argv[1] + "/healthy.json", "w"))
EOF
python3 scripts/bounce_audit.py --bundle "$tmp/healthy.json" > "$tmp/healthy.txt"
grep -q "No flow is above the bounce thresholds" "$tmp/healthy.txt" && ! grep -q "Manual" "$tmp/healthy.txt" || fail "healthy account"
ok "healthy account reports no action"

# 6. Gradual list decay with old profiles: list hygiene, not bots.
python3 - "$tmp" <<'EOF'
import json, sys
b = json.load(open("examples/data/bundle.json"))
for s in ("weekly", "daily"):
    n = len(b[s]["date_times"])
    for r in b[s]["rows"]:
        r["bounced"] = [min(rc, int(rc * (0.01 + 0.05 * i / n))) for i, rc in enumerate(r["recipients"])]
b["bounces"] = [{"datetime": "2026-10-01T10:00:00+00:00", "email": f"real.person{i % 7}@acme-retail.com",
                 "profile_created": "2024-03-01T00:00:00+00:00", "bounce_type": "hard",
                 "flow_id": "RtA9cK", "active_on_site": True} for i in range(40)]
json.dump(b, open(sys.argv[1] + "/decay.json", "w"))
EOF
python3 scripts/bounce_audit.py --bundle "$tmp/decay.json" > "$tmp/decay.txt"
grep -q "LIKELY LIST-HYGIENE PROBLEM" "$tmp/decay.txt" && ! grep -q "hCaptcha" "$tmp/decay.txt" || { cat "$tmp/decay.txt"; fail "decay classified as bots"; }
ok "gradual decay is classified as list hygiene"

# 7. Bad input is rejected with exit 2.
python3 - "$tmp" <<'EOF'
import json, sys
b = json.load(open("examples/data/bundle.json"))
b["weekly"]["rows"][0]["bounced"][0] = b["weekly"]["rows"][0]["recipients"][0] + 1
json.dump(b, open(sys.argv[1] + "/bad.json", "w"))
EOF
set +e; python3 scripts/bounce_audit.py --bundle "$tmp/bad.json" > /dev/null 2> "$tmp/err.txt"; rc=$?; set -e
[ "$rc" = 2 ] && grep -q "bounced exceeds recipients" "$tmp/err.txt" || fail "invalid bundle accepted"
ok "invalid bundle is rejected"

# 8. The report never prints a full email address.
python3 - "$tmp/out.txt" <<'EOF'
import json, sys
text = open(sys.argv[1]).read()
leaks = [e["email"] for e in json.load(open("examples/data/bundle.json"))["bounces"] if e["email"] in text]
sys.exit(1 if leaks else 0)
EOF
ok "no unmasked email addresses in the report"

echo "all $pass checks passed"
