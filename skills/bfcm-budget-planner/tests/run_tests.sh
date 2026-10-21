#!/usr/bin/env bash
# End-to-end checks for BFCM Holiday Budget Planner. No network or credentials needed.
#   bash tests/run_tests.sh
#   KEEP_GOING=1 bash tests/run_tests.sh   reports every failing check instead of stopping at the first
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."
export PYTHONDONTWRITEBYTECODE=1
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
pass=0
failed=0
[ -n "${KEEP_GOING:-}" ] && set +e
ok() { pass=$((pass + 1)); echo "ok $pass - $1"; }
fail() { failed=$((failed + 1)); echo "FAIL - $1"; [ -n "${KEEP_GOING:-}" ] || exit 1; }
H=examples/data/history.csv
E=examples/data/economics.json
ASOF=2026-10-07   # pins "today" (as examples/run.sh does) so the stale-history check is reproducible
run_plan() { python3 scripts/plan_budget.py --as-of "$ASOF" "$@"; }

# Write a variant of the sample economics.json: econ NAME 'python statements on e'
econ() {
  python3 - "$tmp/$1.json" "$2" <<'EOF'
import json, sys
e = json.load(open("examples/data/economics.json"))
exec(sys.argv[2])
json.dump(e, open(sys.argv[1], "w"))
EOF
}

# 1. Sample output is byte-identical to the published expected output.
bash examples/run.sh > "$tmp/out.txt"
cmp -s "$tmp/out.txt" examples/expected_output.txt || { diff "$tmp/out.txt" examples/expected_output.txt | head; fail "sample output differs"; }
ok "sample run matches examples/expected_output.txt"

# 2. Daily plan: channels add up to each day's total, days add up to the report total, total within the cap.
run_plan --history $H --economics $E --csv "$tmp/plan.csv" --json "$tmp/plan.json" > "$tmp/out2.txt"
cmp -s "$tmp/out2.txt" "$tmp/out.txt" || fail "--csv/--json changed the report"
python3 - "$tmp" <<'EOF' || fail "plan totals do not reconcile"
import csv, json, sys
t = sys.argv[1]
rows = list(csv.DictReader(open(t + "/plan.csv")))
a = json.load(open(t + "/plan.json"))
ch = ("meta", "google", "tiktok", "other")
assert len(rows) == 36, len(rows)
for r in rows:
    assert sum(int(r[c + "_budget"]) for c in ch) == int(r["total_budget"]), r["date"]
total = sum(int(r["total_budget"]) for r in rows)
assert total == a["total"]["spend"], (total, a["total"]["spend"])
assert total <= json.load(open("examples/data/economics.json"))["total_budget_cap"], total
assert "**Plan: $%s ad spend" % format(total, ",") in open(t + "/out.txt").read()
EOF
ok "per-day channels sum to the day, days sum to the total, total within the cap"

# 3. No day rises more than the max daily increase (or falls more than the max decrease), starting from today's run rate.
python3 - "$tmp" <<'EOF' || fail "day-over-day limit broken"
import json, sys
a = json.load(open(sys.argv[1] + "/plan.json"))
g, d = a["economics"]["max_daily_increase"], a["economics"]["max_daily_decrease"]
prev = int(a["economics"]["run_rate"])
for r in a["rows"]:
    assert r["total"] <= prev * (1 + g) + 1e-6, (r["date"], r["total"], prev)
    assert r["total"] >= prev * (1 - d) - 1e-6, (r["date"], r["total"], prev)   # whole dollars, limit still held
    prev = r["total"]
EOF
ok "day-over-day increase and decrease never exceed the limits (no -50.01% after Black Friday)"

# 4. Channel shares stay inside min/max every day (allowing $1 of whole-dollar rounding).
python3 - "$tmp" <<'EOF' || fail "channel share outside bounds"
import json, sys
a = json.load(open(sys.argv[1] + "/plan.json"))
b = json.load(open("examples/data/economics.json"))["channel_shares"]
for r in a["rows"]:
    for c, v in r["channels"].items():
        lo, hi = b.get(c, {}).get("min", 0), b.get(c, {}).get("max", 100)
        assert v >= r["total"] * lo / 100 - 1 and v <= r["total"] * hi / 100 + 1, (r["date"], c, v)
EOF
econ tight 'e["channel_shares"] = {"meta": {"min": 30, "max": 40}, "google": {"min": 40, "max": 50}, "tiktok": {"min": 10, "max": 15}, "other": {"min": 0, "max": 5}}'
run_plan --history $H --economics "$tmp/tight.json" --json "$tmp/tight_plan.json" > /dev/null
python3 - "$tmp" <<'EOF' || fail "tight channel bounds not enforced"
import json, sys
a = json.load(open(sys.argv[1] + "/tight_plan.json"))
s = a["shares"]
assert abs(s["meta"] - 0.40) < 1e-9 and 0.40 <= s["google"] <= 0.50 and abs(sum(s.values()) - 1) < 1e-9, s
for r in a["rows"]:
    assert r["channels"]["meta"] <= r["total"] * 0.40 + 1, r
EOF
ok "channel shares stay within min/max, including when the trailing mix breaks them"

# 5. Breakeven and target MER match a hand calculation, on sale days and at full price.
#    60% gross margin, 20% off, 10% returns, 3% fees, $10 shipping, $100 AOV:
#    sale days:  0.90 x (1 - 0.40/0.80) - 0.03 - 0.10 = 0.32 -> breakeven 1/0.32 = 3.125; 12% floor -> 1/0.20 = 5.00
#    full price: 0.90 x (1 - 0.40) - 0.03 - 0.10 = 0.41 -> breakeven 1/0.41 = 2.44; 12% floor -> 1/0.29 = 3.45
econ hand 'e.update(gross_margin_pct=60, discount_pct=20, return_rate_pct=10, payment_fee_pct=3, payment_fee_fixed=0, shipping_fulfillment_per_order=10, bfcm_aov=100, full_price_aov=100); e["target"] = {"type": "margin_floor", "min_contribution_pct": 12}; e.pop("total_budget_cap")'
run_plan --history $H --economics "$tmp/hand.json" --json "$tmp/hand_plan.json" > "$tmp/hand.txt"
python3 - "$tmp" <<'EOF' || fail "breakeven MER differs from the hand calculation"
import json, sys
a = json.load(open(sys.argv[1] + "/hand_plan.json"))["economics"]
assert abs(a["cm"] - 0.32) < 1e-12, a["cm"]
assert abs(a["breakeven_mer"] - 3.125) < 1e-9, a["breakeven_mer"]
assert abs(a["target_mer"] - 5.0) < 1e-9, a["target_mer"]
f = a["full_price"]
assert abs(f["cm"] - 0.41) < 1e-12, f["cm"]
assert abs(f["breakeven_mer"] - 1 / 0.41) < 1e-9 and abs(f["target_mer"] - 1 / 0.29) < 1e-9, f
EOF
grep -q "Contribution before ads\*\* | \*\*32.00\*\* | \*\*41.00\*\*" "$tmp/hand.txt" && grep -q "100 / (32.00 − 12.00) = \*\*5.00\*\*" "$tmp/hand.txt" \
  && grep -q "100 / (41.00 − 12.00) = \*\*3.45\*\* at full price" "$tmp/hand.txt" || fail "hand case not printed"
python3 - "$tmp" <<'EOF' || fail "sale days and full-price days not priced as set, or uncapped plan breaks the margin floor"
import json, sys
a = json.load(open(sys.argv[1] + "/hand_plan.json"))
sw = json.load(open("examples/data/economics.json"))["sale_window"]
for r in a["rows"]:
    sale = sw["start"] <= r["date"] <= sw["end"]
    assert r["on_sale"] == sale and abs(r["contribution_rate"] - (0.32 if sale else 0.41)) < 1e-12, r["date"]
    assert abs(r["contribution"] - (r["contribution_rate"] * r["sales"]["base"] - r["total"])) < 1e-6, r["date"]
t = a["total"]
assert abs(t["base"]["contribution"] - sum(r["contribution"] for r in a["rows"])) < 1e-6
assert t["base"]["margin"] >= 0.12 - 1e-6, t["base"]["margin"]
EOF
ok "breakeven/target MER match the hand calculation at 20% off (3.125/5.00) and full price (2.44/3.45); sale days priced as set; uncapped plan holds the floor"

# 6. A discount deep enough that no MER breaks even is called out.
econ deep 'e["discount_pct"] = 60'
run_plan --history $H --economics "$tmp/deep.json" > "$tmp/deep.txt"
grep -q "Breakeven MER is unreachable: at 60% off, each order loses money" "$tmp/deep.txt" || fail "unreachable breakeven not warned"
ok "unreachable breakeven at 60% off is warned"

# 7. Too little history from this year is warned, and the plan still runs.
python3 - "$tmp" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
keep = [l for l in lines if not l.startswith("2026-") or l >= "2026-09-17"]
open(sys.argv[1] + "/short.csv", "w").write("\n".join(keep) + "\n")
EOF
run_plan --history "$tmp/short.csv" --economics $E > "$tmp/short.txt"
grep -q "History too short: only 20 days from this year" "$tmp/short.txt" || fail "short history not warned"
ok "short history is warned"

# 8. Budget cap below the ramp minimum (today's run rate x 36 days) is warned and still respected.
econ small 'e["total_budget_cap"] = 30000'
run_plan --history $H --economics "$tmp/small.json" --csv "$tmp/small.csv" > "$tmp/small.txt"
grep -q "is below the ramp minimum" "$tmp/small.txt" || fail "cap below ramp minimum not warned"
python3 - "$tmp" <<'EOF' || fail "small cap exceeded"
import csv, sys
assert sum(int(r["total_budget"]) for r in csv.DictReader(open(sys.argv[1] + "/small.csv"))) <= 30000
EOF
ok "budget cap below the ramp minimum is warned and never exceeded"

# 9. Invalid input exits 2 with the reason.
python3 - "$tmp" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
cells = lines[5].split(","); cells[4] = "-120.00"; lines[5] = ",".join(cells)
open(sys.argv[1] + "/neg.csv", "w").write("\n".join(lines) + "\n")
drop = lambda l, i: ",".join(c for j, c in enumerate(l.split(",")) if j != i)
open(sys.argv[1] + "/nocol.csv", "w").write("\n".join(drop(l, 2) for l in lines) + "\n")            # total_orders
open(sys.argv[1] + "/nospend.csv", "w").write("\n".join(",".join(l.split(",")[:4]) for l in lines) + "\n")
EOF
set +e
run_plan --history "$tmp/neg.csv" --economics $E > /dev/null 2> "$tmp/e1"; r1=$?
run_plan --history "$tmp/nocol.csv" --economics $E > /dev/null 2> "$tmp/e2"; r2=$?
econ badshare 'e["channel_shares"]["meta"] = {"min": 80, "max": 90}'
run_plan --history $H --economics "$tmp/badshare.json" > /dev/null 2> "$tmp/e3"; r3=$?
run_plan --history "$tmp/nospend.csv" --economics $E > /dev/null 2> "$tmp/e4"; r4=$?
econ badsale 'e["sale_window"] = {"start": "2026-12-01", "end": "2026-11-24"}'
run_plan --history $H --economics "$tmp/badsale.json" > /dev/null 2> "$tmp/e5"; r5=$?
set -e
[ "$r1" = 2 ] && grep -q "line 6: negative meta_spend" "$tmp/e1" || fail "negative spend accepted (rc=$r1)"
[ "$r2" = 2 ] && grep -q "missing column(s): total_orders" "$tmp/e2" || fail "missing column accepted (rc=$r2)"
[ "$r3" = 2 ] && grep -q "minimums add up to more than 100%" "$tmp/e3" || fail "impossible shares accepted (rc=$r3)"
[ "$r4" = 2 ] && grep -q "has no ad spend column" "$tmp/e4" || fail "history without spend accepted (rc=$r4)"
[ "$r5" = 2 ] && grep -q "sale_window.start is after sale_window.end" "$tmp/e5" || fail "backwards sale_window accepted (rc=$r5)"
ok "invalid input exits 2 with the reason"

# 10. Planning from platform ROAS is refused; platform columns in history are ignored with a warning.
econ roas 'e["target"] = {"type": "roas", "roas": 4}'
set +e; run_plan --history $H --economics "$tmp/roas.json" > "$tmp/roas.txt" 2> "$tmp/roas.err"; rc=$?; set -e
[ "$rc" = 3 ] && grep -q "will not do that" "$tmp/roas.txt" && grep -q "platform ROAS" "$tmp/roas.err" || fail "ROAS target not refused (rc=$rc)"
python3 - "$tmp" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
out = [lines[0] + ",meta_roas"] + [l + ",9.9" for l in lines[1:]]
open(sys.argv[1] + "/roascol.csv", "w").write("\n".join(out) + "\n")
EOF
run_plan --history "$tmp/roascol.csv" --economics $E > "$tmp/roascol.txt"
grep -q "Ignored platform-reported column(s) meta_roas" "$tmp/roascol.txt" || fail "ROAS column not flagged"
ok "platform ROAS target is refused (exit 3); ROAS columns are ignored with a warning"

# 11. The --csv guardrail columns match the report row by row: each day's pull_back_mer / scale_mer equal the last two
#     columns of that day's daily-plan row, and the phase guardrail table shows exactly their min-max range, with the
#     same breakeven/target MER and check times. On the sample (sale Nov 24-Dec 1 splits the warm-up), with no
#     sale_window (25% off every day) and with a one-day sale.
cp "$tmp/out.txt" "$tmp/g_sample.txt"; cp "$tmp/plan.csv" "$tmp/g_sample.csv"
econ nosale 'e.pop("sale_window")'
econ oneday 'e["sale_window"] = {"start": "2026-11-27", "end": "2026-11-27"}'
for v in nosale oneday; do
  run_plan --history $H --economics "$tmp/$v.json" --csv "$tmp/g_$v.csv" > "$tmp/g_$v.txt"
done
python3 - "$tmp" g_sample g_nosale g_oneday <<'EOF' || fail "CSV guardrail columns differ from the report"
import csv, datetime as dt, sys
t = sys.argv[1]
OLD = ["date", "phase", "tag", "total_budget", "meta_budget", "google_budget", "tiktok_budget", "other_budget",
       "proj_net_sales_low", "proj_net_sales_base", "proj_net_sales_high", "proj_mer_base",
       "proj_contribution_base", "ly_date", "ly_net_sales", "ly_ad_spend"]
NEW = ["breakeven_mer", "target_mer", "pull_back_mer", "scale_mer", "check_times"]

def hhmm(x):                                   # "noon" -> 12:00, "2 pm" -> 14:00, "9 am" -> 09:00
    if x == "noon":
        return "12:00"
    n, ap = x.split()
    return f"{int(n) % 12 + (12 if ap == 'pm' else 0):02d}:00"

def rng(cell):
    lo, _, hi = cell.partition("–")
    return lo, hi or lo

for name in sys.argv[2:]:
    report = open(f"{t}/{name}.txt", encoding="utf-8").read().splitlines()
    header = next(csv.reader(open(f"{t}/{name}.csv")))
    assert header == OLD + NEW, (name, header)
    rows = list(csv.DictReader(open(f"{t}/{name}.csv")))
    year = int(rows[0]["date"][:4])
    day = lambda s: dt.datetime.strptime(f"{s} {year}", "%b %d %Y").date()
    start = report.index(next(l for l in report if l.startswith("| Phase | Dates | Pricing |")))
    table = []
    for line in report[start + 2:]:
        if not line.startswith("|"):
            break
        c = [x.strip() for x in line.strip("|").split("|")]
        a, _, b = c[1].partition("–")
        table.append({"phase": c[0], "from": day(a), "to": day(b or a), "n": 0, "pb": [], "sc": [],
                      "breakeven_mer": "" if c[4] == "—" else c[4], "target_mer": "" if c[5] == "—" else c[5],
                      "pb_rng": rng(c[6]), "sc_rng": rng(c[7]),
                      "check_times": ";".join(hhmm(x) for x in c[8].split(" and "))})
    dstart = report.index(next(l for l in report if l.startswith("| Date | Day | Phase |")))
    daily = {}
    for line in report[dstart + 2:]:
        if not line.startswith("| ") or line.startswith("| **Total**"):
            break
        c = [x.strip() for x in line.strip("|").split("|")]
        daily[day(c[0].split(" **")[0])] = (c[-2], c[-1])
    for r in rows:
        d = dt.date.fromisoformat(r["date"])
        assert daily[d] == (r["pull_back_mer"], r["scale_mer"]), (name, r["date"], daily[d])
        match = [g for g in table if g["from"] <= d <= g["to"]]
        assert len(match) == 1 and match[0]["phase"] == r["phase"], (name, r["date"], match)
        g = match[0]
        g["n"] += 1
        g["pb"].append(float(r["pull_back_mer"])); g["sc"].append(float(r["scale_mer"]))
        for k in ("breakeven_mer", "target_mer", "check_times"):
            assert r[k] == g[k], (name, r["date"], k, r[k], g[k])
    for g in table:
        assert g["n"] == (g["to"] - g["from"]).days + 1, (name, g)
        assert g["pb_rng"] == (f"{min(g['pb']):.2f}", f"{max(g['pb']):.2f}"), (name, g["from"], g["pb_rng"])
        assert g["sc_rng"] == (f"{min(g['sc']):.2f}", f"{max(g['sc']):.2f}"), (name, g["from"], g["sc_rng"])
    assert len(table) == {"g_sample": 4, "g_nosale": 3, "g_oneday": 5}[name], (name, len(table))
    bf = [r for r in rows if r["tag"] == "Black Friday"][0]
    assert bf["check_times"] == "12:00;18:00" and 0 < float(bf["pull_back_mer"]) < float(bf["scale_mer"]), bf
EOF
ok "CSV guardrail columns match each day's daily-plan row and the phase table's min-max ranges"

# 12. A channel with no spend gets no budget and no report column, whether its column is absent or all zero.
python3 - "$tmp" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
assert "other_spend" not in lines[0], lines[0]
open(sys.argv[1] + "/other0.csv", "w").write("\n".join([lines[0] + ",other_spend"] + [l + ",0" for l in lines[1:]]) + "\n")
EOF
run_plan --history "$tmp/other0.csv" --economics $E --csv "$tmp/other0_plan.csv" > "$tmp/other0.txt"
cmp -s "$tmp/other0.txt" "$tmp/out.txt" || fail "an all-zero other_spend column changed the report"
cmp -s "$tmp/other0_plan.csv" "$tmp/plan.csv" || fail "an all-zero other_spend column changed the CSV"
grep -qE "\| Other \||other [0-9$]" "$tmp/out.txt" && fail "unused Other channel shown in the report"
python3 - "$tmp" <<'EOF' || fail "unused channel got budget"
import csv, sys
assert all(r["other_budget"] == "0" for r in csv.DictReader(open(sys.argv[1] + "/plan.csv")))
EOF
ok "a channel with no spend (column absent or all zero) gets no budget and no report column; CSV keeps other_budget = 0"

# 13. [QA 1.1] Guardrails are per day: on every row, pull_back_mer < proj_mer_base < scale_mer (Thanksgiving included),
#     on the sample and its variants. A day planned below breakeven keeps its pull-back line below the plan and is warned.
econ mds 'e["min_daily_spend"] = 4000; e.pop("total_budget_cap")'
run_plan --history $H --economics "$tmp/mds.json" --csv "$tmp/g_mds.csv" > "$tmp/g_mds.txt"
run_plan --history $H --economics "$tmp/hand.json" --csv "$tmp/g_hand.csv" > /dev/null
python3 - "$tmp" g_sample g_nosale g_oneday g_hand g_mds <<'EOF' || fail "a pull-back line sits at or above the day's plan MER (or a scale line at or below it)"
import csv, sys
for name in sys.argv[2:]:
    rows = list(csv.DictReader(open(f"{sys.argv[1]}/{name}.csv")))
    for r in rows:
        m = float(r["proj_mer_base"])
        assert float(r["pull_back_mer"]) < m < float(r["scale_mer"]), (name, r["date"], r["pull_back_mer"], m, r["scale_mer"])
    tg = [r for r in rows if r["tag"] == "Thanksgiving"][0]
    assert float(tg["pull_back_mer"]) < float(tg["proj_mer_base"]), (name, tg)
EOF
grep -q "base-case MER is at or below breakeven MER: those days are planned to lose money after ads" "$tmp/g_mds.txt" \
  || fail "a day planned below breakeven was not warned"
ok "guardrails are per day: pull back < plan MER < scale on every row (Thanksgiving on plan does not trip PULL BACK); below-breakeven days warned"

# 14. [QA 1.2] Zero-spend days leave both sides of the demand index: Sep 20-Oct 1 at $0 spend keeps the index near
#     1.11 (not 1.97), says how many days were left out and warns. An implausible index exits 2 unless accepted.
python3 - "$tmp" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
out, big = [lines[0]], [lines[0]]
for l in lines[1:]:
    c = l.split(",")
    big.append(",".join(c[:1] + ["%.2f" % (float(c[1]) * 6)] + c[2:]) if c[0] >= "2026-09-09" else l)
    if "2026-09-20" <= c[0] <= "2026-10-01":
        c[4:] = ["0"] * len(c[4:])
    out.append(",".join(c))
open(sys.argv[1] + "/zero.csv", "w").write("\n".join(out) + "\n")
open(sys.argv[1] + "/bigidx.csv", "w").write("\n".join(big) + "\n")
EOF
run_plan --history "$tmp/zero.csv" --economics $E --json "$tmp/zero.json" > "$tmp/zero.txt"
python3 - "$tmp" <<'EOF' || fail "zero-spend days still inflate the demand index"
import json, sys
a = json.load(open(sys.argv[1] + "/zero.json"))
assert 1.0 <= a["demand_index"] <= 1.25, a["demand_index"]
assert a["demand_index_skipped_days"] == 12, a["demand_index_skipped_days"]
EOF
grep -q "12 zero-spend days left out of both sales and prediction" "$tmp/zero.txt" \
  && grep -q "12 of the 28 trailing days (43%) had no ad spend and were left out of the demand index" "$tmp/zero.txt" \
  || fail "left-out zero-spend days not reported"
set +e
run_plan --history "$tmp/bigidx.csv" --economics $E > /dev/null 2> "$tmp/bigidx.err"; rc=$?
run_plan --history "$tmp/bigidx.csv" --economics $E --accept-demand-index > "$tmp/bigidx.txt" 2>&1; rc2=$?
[ -n "${KEEP_GOING:-}" ] || set -e
[ "$rc" = 2 ] && grep -q "is outside 0.25–4" "$tmp/bigidx.err" || fail "implausible demand index accepted (rc=$rc)"
[ "$rc2" = 0 ] && grep -q "is outside 0.5–2.0" "$tmp/bigidx.txt" || fail "--accept-demand-index did not plan with a warning (rc=$rc2)"
ok "zero-spend days leave the demand index (1.11 not 1.97) with a count and warning; an index outside 0.25-4 exits 2 unless --accept-demand-index"

# 15. [QA 1.3] Headers are normalised ('Meta Spend' = meta_spend); an unrecognised spend column exits 2, never dropped.
python3 - "$tmp" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
open(sys.argv[1] + "/metasp.csv", "w").write("\n".join([lines[0].replace("meta_spend", " Meta Spend")] + lines[1:]) + "\n")
open(sys.argv[1] + "/fb.csv", "w").write("\n".join([lines[0].replace("meta_spend", "fb_spend_usd")] + lines[1:]) + "\n")
EOF
run_plan --history "$tmp/metasp.csv" --economics $E > "$tmp/metasp.txt"
cmp -s "$tmp/metasp.txt" "$tmp/out.txt" || fail "'Meta Spend' header not read as meta_spend"
set +e; run_plan --history "$tmp/fb.csv" --economics $E > /dev/null 2> "$tmp/fb.err"; rc=$?; [ -n "${KEEP_GOING:-}" ] || set -e
[ "$rc" = 2 ] && grep -q "'fb_spend_usd' look like ad spend but are not a recognised name" "$tmp/fb.err" \
  && grep -q "meta_spend, google_spend, tiktok_spend, other_spend" "$tmp/fb.err" || fail "unrecognised spend column not rejected (rc=$rc)"
ok "'Meta Spend' is read as meta_spend; fb_spend_usd exits 2 and lists the expected names"

# 16. [QA 0.1] Strict numbers: comma decimals, nan, inf and exponents exit 2 naming file, line and column; a European-format
#     file fails with its own message; economics NaN/Infinity/'2,9' exit 2; $1,234.56, 1234.56 and EUR1,234.56 parse.
python3 - "$tmp" <<'EOF'
import csv, sys
t = sys.argv[1]
rows = list(csv.reader(open("examples/data/history.csv")))
def write(name, rws):
    with open(f"{t}/{name}.csv", "w", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rws)
for name, v in (("cd", "2255,74"), ("nan", "nan"), ("inf", "inf"), ("exp", "1e9"), ("paren", "(50.00)"),
                ("usd", "$1,234.56"), ("plain", "1234.56"), ("eur", "€1,234.56")):
    r = [list(x) for x in rows]
    r[5][1] = v
    write(name, r)
eu = [rows[0]]
for x in rows[1:]:
    eu.append([x[0]] + [("{:,.2f}".format(float(v)).replace(",", "X").replace(".", ",").replace("X", ".")
                         if "." in v else v) for v in x[1:]])
write("euro", eu)
lines = open("examples/data/history.csv").read().splitlines()
c = lines[5].split(","); c[1] = "2255,74"; lines[5] = ",".join(c)            # hand-edited, unquoted
open(f"{t}/cdraw.csv", "w").write("\n".join(lines) + "\n")
EOF
set +e
for v in cd nan inf exp paren; do
  run_plan --history "$tmp/$v.csv" --economics $E > /dev/null 2> "$tmp/$v.err"; echo $? > "$tmp/$v.rc"
done
run_plan --history "$tmp/euro.csv" --economics $E > /dev/null 2> "$tmp/euro.err"; rce=$?
run_plan --history "$tmp/cdraw.csv" --economics $E > /dev/null 2> "$tmp/cdraw.err"; rcr=$?
econ jnan 'e["gross_margin_pct"] = float("nan")'
econ jinf 'e["total_budget_cap"] = float("inf")'
econ jcomma 'e["payment_fee_pct"] = "2,9"'
econ jstr 'e["shipping_fulfillment_per_order"] = "$11.00"'
for v in jnan jinf jcomma; do
  run_plan --history $H --economics "$tmp/$v.json" > /dev/null 2> "$tmp/$v.err"; echo $? > "$tmp/$v.rc"
done
run_plan --history $H --economics "$tmp/jstr.json" > "$tmp/jstr.txt"; rcs=$?
[ -n "${KEEP_GOING:-}" ] || set -e
for v in cd nan inf exp; do
  [ "$(cat "$tmp/$v.rc")" = 2 ] && grep -q "$v.csv line 6, column 2 (net_sales)" "$tmp/$v.err" || fail "$v cell not rejected with file/line/column (rc=$(cat "$tmp/$v.rc"))"
done
grep -q "decimal separator" "$tmp/cd.err" || fail "comma-decimal message does not suggest '.' as the decimal separator"
[ "$(cat "$tmp/paren.rc")" = 2 ] && grep -q "line 6: negative net_sales" "$tmp/paren.err" || fail "(50.00) accepted where negatives are invalid"
[ "$rce" = 2 ] && grep -q "European number format" "$tmp/euro.err" || fail "European-format file not rejected (rc=$rce)"
[ "$rcr" = 2 ] && grep -q "cdraw.csv line 6 has 8 cells but the header has 7; a comma inside an unquoted number" "$tmp/cdraw.err" \
  || fail "unquoted 2255,74 (an extra cell) accepted (rc=$rcr)"
for v in jnan jinf jcomma; do
  [ "$(cat "$tmp/$v.rc")" = 2 ] || fail "economics $v accepted (rc=$(cat "$tmp/$v.rc"))"
done
grep -q "finite" "$tmp/jnan.err" && grep -q "comma-decimal" "$tmp/jcomma.err" || fail "economics number errors unclear"
[ "$rcs" = 0 ] && cmp -s "$tmp/jstr.txt" "$tmp/out.txt" || fail "economics string \$11.00 not parsed"
python3 -B - "$tmp" <<'EOF' || fail "valid number formats not parsed"
import sys
sys.path.insert(0, "scripts")
import plan_budget as pb
t = sys.argv[1]
for name in ("usd", "plain", "eur"):
    rows, _ = pb.load_history(f"{t}/{name}.csv")
    r = [x for x in rows if x["date"].isoformat() == "2025-10-05"][0]
    assert abs(r["net_sales"] - 1234.56) < 1e-9, (name, r["net_sales"])
assert pb.strict_number("(50.00)", allow_negative=True) == -50.0
assert pb.strict_number("$1,234.56") == 1234.56 and pb.strict_number("€1,234.56") == 1234.56
for bad in ("2255,74", "28.320,00", "nan", "inf", "1e9", "1,23"):
    try:
        pb.strict_number(bad)
    except pb.NumberFormatError:
        continue
    raise AssertionError(bad)
EOF
ok "strict numbers: comma decimal (quoted or not), nan, inf, 1e9 exit 2 with file/line/column; European file rejected; economics NaN/Infinity/'2,9' exit 2; \$1,234.56, 1234.56, €1,234.56 parse"

# 17. [QA 1.4] Paused ads (no spend in the last 14 days) warn and ramp from the last 14 days with spend, not a $0 plan;
#     --starting-daily-budget sets the starting point.
python3 - "$tmp" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
out = [lines[0]]
for l in lines[1:]:
    c = l.split(",")
    if c[0] >= "2026-09-23":
        c[4:] = ["0"] * len(c[4:])
    out.append(",".join(c))
open(sys.argv[1] + "/paused.csv", "w").write("\n".join(out) + "\n")
EOF
run_plan --history "$tmp/paused.csv" --economics $E --csv "$tmp/paused_plan.csv" > "$tmp/paused.txt"
run_plan --history "$tmp/paused.csv" --economics $E --starting-daily-budget 800 --csv "$tmp/paused800.csv" > "$tmp/paused800.txt"
grep -q "Ads were paused in the last 14 days (run rate \$0/day). The ramp starts from" "$tmp/paused.txt" || fail "paused ads not warned"
grep -q "Spend stops at: marginal breakeven" "$tmp/paused.txt" && fail "paused ads still give the marginal-breakeven reason"
grep -q "ramp starts from \$800/day (--starting-daily-budget)" "$tmp/paused800.txt" || fail "--starting-daily-budget not used"
python3 - "$tmp" <<'EOF' || fail "paused ads still give a \$0 plan"
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1] + "/paused_plan.csv")))
assert sum(int(r["total_budget"]) for r in rows) > 0 and int(rows[0]["total_budget"]) > 0
rows = list(csv.DictReader(open(sys.argv[1] + "/paused800.csv")))
assert int(rows[0]["total_budget"]) == 800, rows[0]["total_budget"]
EOF
ok "paused ads warn and ramp from the last 14 days with spend (or --starting-daily-budget), not a \$0 plan"

# 18. [QA 1.5] A clamped elasticity is labelled assumed, listed under Warnings, and the Black Friday budget is flagged
#     as resting on an assumed curve, with a sensitivity line at elasticity 0.40.
grep -q "| cyber week | 6 | .* | 0.70 | 0.98 | assumed 0.70 (clamped from 1.06); demand rose with spend |" "$tmp/out.txt" \
  && grep -q "^- Cyber week elasticity is assumed, not fitted: last year's raw fit was 1.06" "$tmp/out.txt" \
  && grep -q "^- The Black Friday budget rests on an assumed cyber-week curve" "$tmp/out.txt" \
  && grep -q "^Sensitivity: at elasticity 0.40 in every phase" "$tmp/out.txt" || fail "clamped elasticity not reported as assumed"
python3 - "$tmp" <<'EOF' || fail "clamped fit still marked fitted"
import json, sys
f = json.load(open(sys.argv[1] + "/plan.json"))["fits"]
assert f["cyber week"]["fitted"] is False and f["cyber week"]["clamped"] is True and abs(f["cyber week"]["raw_b"] - 1.06) < 0.005
assert f["taper"]["fitted"] is True and f["taper"]["clamped"] is False
EOF
ok "clamped elasticities are labelled assumed (clamped from X), warned, with a 0.40 sensitivity line"

# 19. [QA 1.6] Stale history, missing trailing days, and a Black Friday that isn't a Friday.
python3 - "$tmp" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
keep = [l for l in lines if not ("2026-09-20" <= l[:10] <= "2026-09-27")]
open(sys.argv[1] + "/gaps.csv", "w").write("\n".join(keep) + "\n")
EOF
python3 scripts/plan_budget.py --history $H --economics $E --as-of 2026-10-25 > "$tmp/stale.txt"
run_plan --history "$tmp/gaps.csv" --economics $E > "$tmp/gaps.txt"
econ thu 'e["black_friday"] = "2026-11-26"'
econ cmbad 'e["cyber_monday"] = "2026-12-01"'
set +e
run_plan --history $H --economics "$tmp/thu.json" > /dev/null 2> "$tmp/thu.err"; r1=$?
run_plan --history $H --economics "$tmp/cmbad.json" > /dev/null 2> "$tmp/cmbad.err"; r2=$?
[ -n "${KEEP_GOING:-}" ] || set -e
grep -q "History is stale: it ends 2026-10-06, 19 days before today (2026-10-25)" "$tmp/stale.txt" || fail "stale history not warned"
grep -q "History is stale" "$tmp/out.txt" && fail "fresh sample history warned as stale"
grep -q "History is missing 8 of the last 28 days" "$tmp/gaps.txt" || fail "missing trailing days not warned"
[ "$r1" = 2 ] && grep -q "black_friday 2026-11-26 is a Thursday, not a Friday" "$tmp/thu.err" || fail "Thursday black_friday accepted (rc=$r1)"
[ "$r2" = 2 ] && grep -q "cyber_monday 2026-12-01 must be the Monday after black_friday" "$tmp/cmbad.err" || fail "wrong cyber_monday accepted (rc=$r2)"
ok "stale history and 7+ missing trailing days are warned; black_friday must be a Friday and cyber_monday the Monday after"

# 20. [QA 1.7] cp1252 stdout doesn't crash; a full-price AOV is used for full-price days; no-organic-baseline note.
PYTHONIOENCODING=cp1252 bash examples/run.sh > "$tmp/cp.txt" 2> "$tmp/cp.err" || fail "crashes with PYTHONIOENCODING=cp1252"
cmp -s "$tmp/cp.txt" examples/expected_output.txt || fail "cp1252 run output differs"
econ fpaov 'e["full_price_aov"] = 120'
run_plan --history $H --economics "$tmp/fpaov.json" --json "$tmp/fpaov_plan.json" > "$tmp/fpaov.txt"
python3 - "$tmp" <<'EOF' || fail "full_price_aov not used for full-price days"
import json, sys
ec = json.load(open(sys.argv[1] + "/fpaov_plan.json"))["economics"]
assert ec["full_price"]["aov"] == 120 and abs(ec["full_price"]["lines"]["shipping_fulfillment"] + 11 / 120) < 1e-12, ec["full_price"]
EOF
grep -q "There is no organic baseline" "$tmp/out.txt" || fail "no-organic-baseline note missing"
ok "cp1252 output works; full_price_aov prices full-price days; the no-organic-baseline limit is stated"


# 21. [QA re-review B-2] Platform export headers ('Amount spent' from Meta, 'Cost' from Google/TikTok) exit 2,
#     never silently dropped (dropping Meta spend made MER 6.00 and planned to the cap).
for hdr in "Amount spent" "Cost" "amount_spent_usd"; do
  python3 - "$tmp" "$hdr" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
open(sys.argv[1] + "/plat.csv", "w").write("\n".join([lines[0].replace("meta_spend", sys.argv[2])] + lines[1:]) + "\n")
EOF
  set +e; run_plan --history "$tmp/plat.csv" --economics $E > /dev/null 2> "$tmp/plat.err"; rc=$?; [ -n "${KEEP_GOING:-}" ] || set -e
  [ "$rc" = 2 ] && grep -q "look like ad spend but are not a recognised name" "$tmp/plat.err" \
    || fail "platform export header '$hdr' was not rejected (rc=$rc)"
done
ok "Meta 'Amount spent', 'Cost' and 'amount_spent_usd' headers exit 2 instead of being dropped"

# 22. [QA re-review S-P1] A channel column with no spend-like word in its name ('Facebook Ads', 'Meta', 'FB',
#     'Kosten', 'Importe gastado') holds numbers the plan would drop: exit 2, never a silent $86,992 / MER 6.00
#     plan. Known non-spend store columns (sessions, gross_sales, ...) and text columns are still allowed.
for hdr in "Facebook Ads" "Meta" "FB" "Kosten" "Importe gastado"; do
  python3 - "$tmp" "$hdr" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
open(sys.argv[1] + "/chan.csv", "w").write("\n".join([lines[0].replace("meta_spend", sys.argv[2])] + lines[1:]) + "\n")
EOF
  set +e; run_plan --history "$tmp/chan.csv" --economics $E > "$tmp/chan.out" 2> "$tmp/chan.err"; rc=$?; [ -n "${KEEP_GOING:-}" ] || set -e
  [ "$rc" = 2 ] && grep -q "hold numbers but are not a recognised column" "$tmp/chan.err" \
    || fail "numeric column '$hdr' was dropped silently (rc=$rc)"
  ! grep -q "86,992" "$tmp/chan.out" || fail "plan printed without Meta spend for '$hdr'"
done
python3 - "$tmp" <<'EOF'
import sys
lines = open("examples/data/history.csv").read().splitlines()
out = [lines[0] + ",Sessions,gross_sales,notes"] + [l + ",1200,2500.00,promo day" for l in lines[1:]]
open(sys.argv[1] + "/extra.csv", "w").write("\n".join(out) + "\n")
EOF
set +e; run_plan --history "$tmp/extra.csv" --economics $E > /dev/null 2> "$tmp/extra.err"; rc=$?; [ -n "${KEEP_GOING:-}" ] || set -e
[ "$rc" = 0 ] || fail "known non-spend columns (Sessions, gross_sales) or a text column were rejected (rc=$rc)"
ok "numeric channel columns without a spend word ('Facebook Ads', 'Meta', 'FB', 'Kosten', 'Importe gastado') exit 2"

[ "$failed" = 0 ] || { echo "$failed check(s) failed"; exit 1; }
echo "all $pass checks passed"
