import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class PulseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = pathlib.Path(self.tmp.name)

    def run_pulse(self, quiet_sample=False, overrides=None, extra=()):
        data = ROOT / ("examples/quiet/data" if quiet_sample else "examples/data")
        paths = {"plan": ROOT/"examples/data/plan.csv", "hourly": data/"hourly.csv",
                 "ly-hourly": data/"ly_hourly.csv", "spend": data/"spend_today.csv",
                 "discounts": data/"discount_usage.csv", "inventory": data/"inventory.csv",
                 "config": data/"config.json"}
        paths.update(overrides or {})
        output = self.folder/"result.json"
        cmd = [sys.executable, str(ROOT/"scripts/live_pulse.py")]
        for key, value in paths.items():
            if value is not None:
                cmd += ["--"+key, str(value)]
        cmd += ["--json", str(output)] + list(extra)
        if output.exists():
            output.unlink()
        result = subprocess.run(cmd, text=True, capture_output=True)
        return result, json.loads(output.read_text()) if output.exists() else None

    def change_spend(self, change):
        with (ROOT/"examples/data/spend_today.csv").open() as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        rows = change(rows)
        path = self.folder/"spend.csv"
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def boost_sales(self, factor):
        """Sample hourly file with net sales multiplied by factor (spend stays on pace, so MER rises)."""
        with (ROOT/"examples/data/hourly.csv").open() as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        path = self.folder/"hourly-boost.csv"
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows([dict(r, net_sales="%.2f" % (float(r["net_sales"]) * factor)) for r in rows])
        return path

    def scale_spend(self, channel, factor):
        """Sample spend with one platform's spend multiplied by factor (a lagging or partial read)."""
        return self.change_spend(lambda rows: [dict(r, spend="%.2f" % (float(r["spend"]) * factor))
                                               if r["platform"] == channel else r for r in rows])

    def config(self, **changes):
        config = json.loads((ROOT/"examples/data/config.json").read_text())
        config.update(changes)
        path = self.folder/"config-{}.json".format(len(list(self.folder.glob("config-*.json"))))
        path.write_text(json.dumps(config))
        return path

    def test_sample_math_and_alerts(self):
        result, a = self.run_pulse()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(a["counts"], {"critical":1,"warning":2,"resolved":1})
        # MER is aligned to the earliest spend read (Google, 13:20): sales through 13:20, with later
        # reads scaled back to 13:20 along last year's curve (QA 3.2)
        self.assertEqual(a["mer"]["cutoff"], "13:20")
        with (ROOT/"examples/data/hourly.csv").open() as f:
            hourly = {int(r["hour"]): float(r["net_sales"])
                      for r in csv.DictReader(f)}
        sales_cut = sum(hourly[h] for h in range(13)) + hourly[13] * 20 / 60
        self.assertAlmostEqual(a["mer"]["sales_at_cutoff"], sales_cut, places=4)
        self.assertLessEqual(a["mer"]["spend_at_cutoff"], 3034.4)
        self.assertAlmostEqual(a["mer"]["mer"], sales_cut / a["mer"]["spend_at_cutoff"], places=5)
        self.assertEqual(a["mer"]["call"], "HOLD")
        self.assertEqual(a["plan"]["format"], "bfcm-budget-planner")
        self.assertLessEqual(len(result.stdout), 1800)
        self.assertEqual(result.stdout, (ROOT/"examples/expected_output.txt").read_text())

    def test_quiet_sample(self):
        result, a = self.run_pulse(True, extra=["--quiet"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse(a["quiet"]["printed"])

    def test_alerts_override_quiet(self):
        result, a = self.run_pulse(extra=["--quiet"])
        self.assertTrue(result.stdout)
        self.assertTrue(a["quiet"]["needs_post"])

    def test_stale_spend_blocks_actions_and_state(self):
        p = self.change_spend(lambda rows: [dict(r, read_at="2026-11-27T08:00-05:00") for r in rows])
        state = self.folder/"state.json"
        result, a = self.run_pulse(overrides={"spend":p}, extra=["--quiet","--state",str(state)])
        self.assertEqual(a["mer"]["call"], "DATA INCOMPLETE")
        self.assertEqual(a["mer"]["actions"], [])
        self.assertFalse(state.exists())
        self.assertIn("DATA INCOMPLETE", result.stdout)

    def test_missing_channel_blocks_actions(self):
        p = self.change_spend(lambda rows: rows[:1])
        _, a = self.run_pulse(overrides={"spend":p})
        self.assertEqual(a["mer"]["call"], "DATA INCOMPLETE")
        self.assertEqual(a["spend"]["missing"], ["google","tiktok"])

    def test_missing_hour_is_not_zero_filled(self):
        lines = (ROOT/"examples/data/hourly.csv").read_text().splitlines()
        path = self.folder/"hourly.csv"
        path.write_text("\n".join(lines[:2]+lines[3:])+"\n")
        result, a = self.run_pulse(overrides={"hourly":path})
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(a)
        self.assertIn("no row for hour", result.stderr)

    def test_optional_inputs_explicitly_not_checked(self):
        _, a = self.run_pulse(overrides={"discounts":None,"inventory":None})
        self.assertEqual(len(a["not_checked"]), 2)

    def test_scale_requires_two_checks(self):
        # sales double with spend on pace, so MER is genuinely above the scale line
        hp = self.boost_sales(2)
        _, a = self.run_pulse(overrides={"hourly":hp})
        self.assertEqual(a["spend"]["status"], "on pace")
        self.assertEqual(a["mer"]["call"], "HOLD")
        self.assertTrue(a["mer"]["above_scale"])
        state = self.folder/"state.json"
        prev = a["mer"]["previous_check"]
        state.write_text(json.dumps({"checks":[dict(prev, mer=10, scale_mer=4.9)]}))
        _, a = self.run_pulse(overrides={"hourly":hp}, extra=["--state",str(state)])
        self.assertEqual(a["mer"]["call"], "SCALE")
        self.assertEqual(len(json.loads(state.read_text())["checks"]), 2)

    def test_pullback_does_not_cut_already_spent(self):
        p = self.change_spend(lambda rows: [dict(r, spend=str(float(r["spend"])*2)) for r in rows])
        _, a = self.run_pulse(overrides={"spend":p})
        self.assertEqual(a["mer"]["call"], "PULL BACK")
        for action in a["mer"]["actions"]:
            self.assertGreaterEqual(action["new_daily_budget"]+.5, action["spent"])

    def test_no_spend_has_no_mer(self):
        # $0 for every budgeted channel after the first check is missing data (QA 3.1); when every channel
        # is paused on purpose it is NO SPEND. Either way there is no MER.
        p = self.change_spend(lambda rows: [dict(r, spend="0") for r in rows])
        _, a = self.run_pulse(overrides={"spend":p})
        self.assertEqual(a["mer"]["call"], "DATA INCOMPLETE")
        self.assertIsNone(a["mer"]["mer"])
        cfg = self.config(paused_channels=["meta", "google", "tiktok"])
        _, a = self.run_pulse(overrides={"spend":p, "config":cfg})
        self.assertEqual(a["mer"]["call"], "NO SPEND")
        self.assertIsNone(a["mer"]["mer"])

    def test_invalid_guardrails_rejected(self):
        result, _ = self.run_pulse(extra=["--pull-back-mer","7","--scale-mer","4"])
        self.assertEqual(result.returncode, 2)

    def test_invalid_threshold_rejected(self):
        config = json.loads((ROOT/"examples/data/config.json").read_text())
        config["thresholds"]["cover_hours"] = float("nan")
        p = self.folder/"config.json"
        p.write_text(json.dumps(config))
        result, _ = self.run_pulse(overrides={"config":p})
        self.assertEqual(result.returncode, 2)

    # ------------------------------------------------------------------ QA fixes (v2)
    def write_rows(self, name, fields, rows):
        path = self.folder/name
        with path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(fields)
            w.writerows(rows)
        return path

    def prev_above_scale(self):
        """State whose previous check (yesterday 6 pm) was above the scale line."""
        state = self.folder/"state.json"
        state.write_text(json.dumps({"checks": [{"date": "2026-11-26", "time": "18:00", "mer": 10,
                                                 "scale_mer": 4.9}]}))
        return state

    def spend_at(self, read_at):
        """Spend rows read at one time, each exactly on its expected pace for that time."""
        probe = self.change_spend(lambda rows: [dict(r, read_at=read_at) for r in rows])
        _, a = self.run_pulse(overrides={"spend": probe})
        expected = {c["platforms"][0]: c["expected"] for c in a["spend"]["channels"]}
        return self.change_spend(lambda rows: [dict(r, read_at=read_at,
                                                    spend="%.2f" % expected[r["platform"]]) for r in rows])

    def test_strict_number_parser(self):
        # QA 0.1: comma decimals, nan and inf exit 2 naming file, line and column
        for bad in ("1731,29", "nan", "inf", "1e3"):
            p = self.change_spend(lambda rows: [dict(r, spend=bad) if r["platform"] == "Meta" else r for r in rows])
            result, a = self.run_pulse(overrides={"spend": p})
            self.assertEqual(result.returncode, 2, bad)
            self.assertIsNone(a)
            self.assertIn("spend.csv line 2, column spend", result.stderr)
            if bad == "1731,29":
                self.assertIn("'.' as the decimal separator", result.stderr)
        # accepted forms still parse
        for good in ("$1,731.29", "€1,731.29", "1731.29", " 1,731.29 "):
            p = self.change_spend(lambda rows: [dict(r, spend=good) if r["platform"] == "Meta" else r for r in rows])
            result, a = self.run_pulse(overrides={"spend": p})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertAlmostEqual(a["spend"]["platforms"][0]["spend"], 1731.29)
        # (5) is a negative where negatives are allowed (inventory on_hand) ...
        lines = (ROOT/"examples/data/inventory.csv").read_text().splitlines()
        hdr = lines[0].split(",")
        col = hdr.index("on_hand")
        cells = lines[1].split(",")
        cells[col] = "(5)"
        inv = self.folder/"inventory.csv"
        inv.write_text("\n".join([lines[0], ",".join(cells)] + lines[2:]) + "\n")
        result, a = self.run_pulse(overrides={"inventory": inv})
        self.assertEqual(result.returncode, 0, result.stderr)
        # ... and rejected where they are not (spend)
        p = self.change_spend(lambda rows: [dict(r, spend="(50.00)") if r["platform"] == "Meta" else r for r in rows])
        result, _ = self.run_pulse(overrides={"spend": p})
        self.assertEqual(result.returncode, 2)
        # a clearly European-format file gets its own message
        with (ROOT/"examples/data/hourly.csv").open() as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        def euro(v):
            whole, frac = ("%.2f" % float(v)).split(".")
            return "{:,}".format(int(whole)).replace(",", ".") + "," + frac
        hp = self.write_rows("hourly.csv", fields, [[euro(r[k]) if k == "net_sales" else r[k] for k in fields]
                                                    for r in rows])
        result, _ = self.run_pulse(overrides={"hourly": hp})
        self.assertEqual(result.returncode, 2)
        self.assertIn("European number format", result.stderr)
        self.assertIn("column net_sales", result.stderr)

    def test_negative_hourly_sales_explained(self):
        # QA 0.2: hourly net sales are before returns, so a negative hour is an export error with a clear reason
        lines = (ROOT/"examples/data/hourly.csv").read_text().splitlines()
        cells = lines[1].split(",")
        cells[3] = "-25.00"
        path = self.folder/"hourly.csv"
        path.write_text("\n".join([lines[0], ",".join(cells)] + lines[2:]) + "\n")
        result, _ = self.run_pulse(overrides={"hourly": path})
        self.assertEqual(result.returncode, 2)
        self.assertIn("before returns/refunds", result.stderr)

    def test_zero_spend_budgeted_channel_blocks_scale(self):
        # QA 3.1 [BLOCKER] repro: Meta reads $0 at 13:50 against its budget ($3,693; $3,730 in the QA plan), previous check above scale
        p = self.change_spend(lambda rows: [dict(r, spend="0") if r["platform"] == "Meta" else r for r in rows])
        result, a = self.run_pulse(overrides={"spend": p}, extra=["--state", str(self.prev_above_scale())])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(a["mer"]["call"], "DATA INCOMPLETE")
        self.assertEqual(a["mer"]["actions"], [])
        self.assertNotIn("SCALE", result.stdout)
        self.assertIn("DATA INCOMPLETE", result.stdout)
        # an intentional pause is declared in config: MER is computed and Meta is labelled paused
        cfg = self.config(paused_channels=["meta"])
        result, a = self.run_pulse(overrides={"spend": p, "config": cfg},
                                   extra=["--state", str(self.prev_above_scale())])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(a["mer"]["call"], "DATA INCOMPLETE")
        self.assertIsNotNone(a["mer"]["mer"])
        self.assertEqual(a["spend"]["paused"], ["meta"])
        self.assertEqual(a["spend"]["channels"][0]["status"], "paused")
        self.assertIn("Meta marked paused", result.stdout)

    def test_spend_reads_at_1201_do_not_scale(self):
        # QA 3.2 [BLOCKER] repro: every spend read at 12:01, as-of 14:00, spend exactly on pace
        p = self.spend_at("2026-11-27T12:01-05:00")
        result, a = self.run_pulse(overrides={"spend": p}, extra=["--state", str(self.prev_above_scale())])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(a["mer"]["call"], ("SCALE", "PULL BACK"))
        self.assertEqual(a["mer"]["cutoff"], "12:01")
        self.assertLess(a["mer"]["mer"], a["mer"]["scale_mer"])
        self.assertGreater(a["mer"]["mer"], a["mer"]["pull_back_mer"])

    def test_spend_reads_at_1345_aligned(self):
        p = self.spend_at("2026-11-27T13:45-05:00")
        result, a = self.run_pulse(overrides={"spend": p}, extra=["--state", str(self.prev_above_scale())])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(a["mer"]["call"], ("SCALE", "PULL BACK"))
        self.assertEqual(a["mer"]["cutoff"], "13:45")
        self.assertLess(a["mer"]["mer"], a["mer"]["scale_mer"])
        self.assertGreater(a["mer"]["mer"], a["mer"]["pull_back_mer"])

    def test_spread_out_reads_block_budget_call(self):
        # QA 3.2: reads more than 30 minutes apart never produce SCALE or PULL BACK
        p = self.change_spend(lambda rows: [dict(r, read_at="2026-11-27T13:00-05:00" if r["platform"] == "Google Ads"
                                                 else r["read_at"]) for r in rows])
        result, a = self.run_pulse(overrides={"spend": p, "hourly": self.boost_sales(2)},
                                   extra=["--state", str(self.prev_above_scale())])
        self.assertEqual(a["mer"]["read_spread_min"], 50)
        self.assertEqual(a["mer"]["call"], "HOLD")
        self.assertTrue(a["mer"]["out_of_step"])
        self.assertEqual(a["mer"]["blocked_call"], "SCALE")
        self.assertEqual(a["mer"]["actions"], [])
        self.assertIn("too far apart for a budget call", result.stdout)

    def test_spend_timestamps_need_offset(self):
        # QA 3.3: a UTC time written without an offset is rejected unless config says spend is store time
        p = self.change_spend(lambda rows: [dict(r, read_at="2026-11-27 13:50") for r in rows])
        result, _ = self.run_pulse(overrides={"spend": p})
        self.assertEqual(result.returncode, 2)
        self.assertIn("no UTC offset", result.stderr)
        result, a = self.run_pulse(overrides={"spend": p, "config": self.config(spend_timestamps_tz="store")})
        self.assertEqual(result.returncode, 0, result.stderr)
        # reads more than 5 minutes after the as-of time are rejected
        p = self.change_spend(lambda rows: [dict(r, read_at="2026-11-27T14:10-05:00") for r in rows])
        result, _ = self.run_pulse(overrides={"spend": p})
        self.assertEqual(result.returncode, 2)

    def test_action_lines_need_approval(self):
        # QA 3.4
        p = self.change_spend(lambda rows: [dict(r, spend=str(float(r["spend"])*2)) for r in rows])
        result, a = self.run_pulse(overrides={"spend": p})
        self.assertEqual(a["mer"]["call"], "PULL BACK")
        self.assertIn("Proposed (needs approval): *PULL BACK*", result.stdout)

    def test_duplicate_and_alias_platforms(self):
        # QA 3.5: same platform twice at one read time exits 2; Meta + Facebook is flagged as possible double count
        p = self.change_spend(lambda rows: rows + [dict(rows[0])])
        result, _ = self.run_pulse(overrides={"spend": p})
        self.assertEqual(result.returncode, 2)
        self.assertIn("appears twice", result.stderr)
        p = self.change_spend(lambda rows: rows + [dict(rows[0], platform="Facebook", spend="10")])
        result, a = self.run_pulse(overrides={"spend": p})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("counted twice" in n for n in a["notes"]))

    def test_no_sales_shows_mer_na(self):
        # QA 3.5: no sales yet -> "MER n/a", never MER 0.00 or a PULL BACK
        with (ROOT/"examples/data/hourly.csv").open() as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        hp = self.write_rows("hourly.csv", fields, [[("0" if k in ("orders", "net_sales") else r[k]) for k in fields]
                                                    for r in rows])
        result, a = self.run_pulse(overrides={"hourly": hp})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(a["mer"]["mer"])
        self.assertEqual(a["mer"]["call"], "NO SALES")
        self.assertIn("MER n/a", result.stdout)
        self.assertNotIn("0.00", result.stdout)

    def test_on_plan_thanksgiving_holds(self):
        # QA 3.6 / Planner 1.1: actuals exactly on the planner's Thanksgiving row must not PULL BACK.
        # Reads tests/fixtures/planner_sample_plan.csv, the fixed planner's sample CSV (per-day guardrails).
        plan_path = ROOT/"tests/fixtures/planner_sample_plan.csv"
        with plan_path.open() as f:
            row = next(r for r in csv.DictReader(f) if r["date"] == "2026-11-26")
        weights = [3, 2, 1, 1, 1, 1, 2, 3, 4, 5, 6, 6, 6, 6, 6, 6, 7, 8, 9, 9, 8, 6, 5, 4]
        ly_total = float(row["ly_net_sales"])
        ly_sales = [ly_total * w / sum(weights) for w in weights]
        ly_orders = [round(s / 80) for s in ly_sales]
        growth = float(row["proj_net_sales_base"]) / sum(ly_sales)
        ly = self.write_rows("ly.csv", ["date", "hour", "orders", "net_sales"],
                             [[row["ly_date"], h, ly_orders[h], "%.4f" % ly_sales[h]] for h in range(24)])
        td = self.write_rows("td.csv", ["date", "hour", "orders", "net_sales"],
                             [["2026-11-26", h, round(ly_orders[h] * growth), "%.4f" % (ly_sales[h] * growth)]
                              for h in range(18)])
        share = sum(ly_sales[:18]) / sum(ly_sales)
        spend = self.write_rows("sp.csv", ["platform", "spend", "read_at"],
                                [[name, "%.4f" % (float(row[col]) * share), "2026-11-26T18:00-05:00"]
                                 for name, col in (("Meta", "meta_budget"), ("Google Ads", "google_budget"),
                                                   ("TikTok", "tiktok_budget"))])
        cfg = self.config(as_of="2026-11-26 18:00")
        result, a = self.run_pulse(overrides={"plan": plan_path, "hourly": td, "ly-hourly": ly, "spend": spend,
                                              "discounts": None, "inventory": None, "config": cfg})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertAlmostEqual(a["mer"]["mer"], float(row["proj_mer_base"]), delta=0.02)
        self.assertNotEqual(a["mer"]["call"], "PULL BACK",
                            "on-plan Thanksgiving gave PULL BACK: pull_back_mer %s >= plan MER %s in %s"
                            % (row["pull_back_mer"], row["proj_mer_base"], plan_path))
        self.assertEqual(a["mer"]["call"], "HOLD")
        self.assertNotEqual(a["status"], "ACTION NEEDED")

    def test_scaled_spend_needs_raw_agreement(self):
        # QA re-review: scaling later reads back to the cut-off only removes spend, so it can only raise MER.
        # Sales spike at noon and are exactly on plan; Google is read at 12:05, Meta and TikTok at 12:30.
        # Scaled MER clears the scale line but raw MER (spend as read) sits on plan, so the call is HOLD.
        weights = [2, 1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 6, 40, 8, 6, 6, 6, 6, 6, 6, 5, 4, 3, 2]
        ly_total = 30000.0
        ly_sales = [ly_total * w / sum(weights) for w in weights]
        ly = self.write_rows("ly.csv", ["date", "hour", "orders", "net_sales"],
                             [["2025-11-28", h, max(1, round(ly_sales[h] / 88)), "%.4f" % ly_sales[h]] for h in range(24)])
        td = self.write_rows("td.csv", ["date", "hour", "orders", "net_sales"],
                             [["2026-11-27", h, max(1, round(ly_sales[h] * 1.046 / 88)), "%.4f" % (ly_sales[h] * 1.046)]
                              for h in range(13)])
        sales_cut = sum(ly_sales[h] * 1.046 for h in range(12)) + ly_sales[12] * 1.046 * 5 / 60
        raw_spend = sales_cut / 4.71                      # raw MER exactly on plan
        split = (("Meta", 0.55, "12:30"), ("Google Ads", 0.32, "12:05"), ("TikTok", 0.13, "12:30"))
        spend = self.write_rows("sp.csv", ["platform", "spend", "read_at"],
                                [[n, "%.2f" % (raw_spend * w), "2026-11-27T%s-05:00" % t] for n, w, t in split])
        # the noon spike puts Meta/TikTok at ~68% of expected spend by 12:30, which the spend-behind guard (S-L1)
        # would also block; widen the pace band so this test isolates the raw-agreement rule
        th = json.loads((ROOT/"examples/data/config.json").read_text())["thresholds"]
        cfg = self.config(as_of="2026-11-27 13:00", thresholds=dict(th, spend_pace_band_pct=40))
        result, a = self.run_pulse(overrides={"hourly": td, "ly-hourly": ly, "spend": spend,
                                              "discounts": None, "inventory": None, "config": cfg},
                                   extra=["--state", str(self.prev_above_scale())])
        self.assertEqual(result.returncode, 0, result.stderr)
        g = a["mer"]
        self.assertGreater(g["mer"], g["scale_mer"])           # scaled MER alone would say SCALE
        self.assertAlmostEqual(g["mer_raw"], 4.71, delta=0.02)
        self.assertEqual(g["call"], "HOLD")
        self.assertTrue(g["raw_disagrees"])
        self.assertEqual(g["blocked_call"], "SCALE")
        self.assertEqual(g["actions"], [])
        self.assertIn("the two disagree, so no budget call until they agree", result.stdout)

    def test_scale_blocked_while_spend_behind(self):
        # S-L1: a lagging Meta read (x0.03, x0.10, x0.30 of its spend) inflates MER, scaled and raw alike, past the
        # scale line with the previous check above it. SCALE must not be proposed while spend is behind pace.
        for factor in (0.03, 0.10, 0.30):
            p = self.scale_spend("Meta", factor)
            state = self.prev_above_scale()
            result, a = self.run_pulse(overrides={"spend": p}, extra=["--state", str(state)])
            self.assertEqual(result.returncode, 0, result.stderr)
            g = a["mer"]
            self.assertGreater(g["mer"], g["scale_mer"], factor)
            self.assertGreater(g["mer_raw"], g["scale_mer"], factor)
            self.assertEqual(g["call"], "HOLD", factor)
            self.assertTrue(g["spend_behind"], factor)
            self.assertEqual(g["blocked_call"], "SCALE", factor)
            self.assertEqual(g["actions"], [], factor)
            self.assertTrue(any(r.startswith("Meta ") for r in g["spend_behind_reasons"]), factor)
            self.assertNotIn("*SCALE*", result.stdout, factor)
            self.assertNotIn("Raise tomorrow", result.stdout, factor)
            self.assertIn("but spend is behind", result.stdout, factor)
            self.assertIsNone(g["state_reading"], factor)     # the inflated reading is not saved as a check
            self.assertEqual(len(json.loads(state.read_text())["checks"]), 1, factor)
        # one budgeted channel far behind while the total is inside the band still blocks SCALE
        th = json.loads((ROOT/"examples/data/config.json").read_text())["thresholds"]
        cfg = self.config(thresholds=dict(th, spend_pace_band_pct=60))
        result, a = self.run_pulse(overrides={"spend": self.scale_spend("Meta", 0.30), "config": cfg},
                                   extra=["--state", str(self.prev_above_scale())])
        self.assertNotEqual(a["spend"]["status"], "behind")
        self.assertEqual(a["mer"]["call"], "HOLD")
        self.assertTrue(a["mer"]["spend_behind"])
        # spend on pace with sales genuinely high still scales
        result, a = self.run_pulse(overrides={"hourly": self.boost_sales(2)},
                                   extra=["--state", str(self.prev_above_scale())])
        self.assertEqual(a["mer"]["call"], "SCALE")
        self.assertFalse(a["mer"]["spend_behind"])

    def test_simultaneous_reads_in_incomplete_hour(self):
        # S-L2: all reads at one time inside an incomplete hour are 0 min apart; the capped cut-off (13:00) must not
        # make them "too far apart". Spend x1.35 reads below the pull-back line, scaled and raw.
        th_cfg = self.config(as_of="2026-11-27 13:55")
        for t in ("13:48", "13:31", "13:10"):
            p = self.change_spend(lambda rows: [dict(r, spend="%.2f" % (float(r["spend"]) * 1.35),
                                                     read_at="2026-11-27T%s-05:00" % t) for r in rows])
            result, a = self.run_pulse(overrides={"spend": p, "config": th_cfg, "discounts": None, "inventory": None})
            self.assertEqual(result.returncode, 0, result.stderr)
            g = a["mer"]
            self.assertEqual(g["cutoff"], "13:00", t)
            self.assertEqual(g["read_spread_min"], 0, t)
            self.assertFalse(g["out_of_step"], t)
            self.assertLess(g["mer"], g["pull_back_mer"], t)
            self.assertEqual(g["call"], "PULL BACK", t)
            self.assertNotIn("too far apart", result.stdout, t)
        # reads genuinely 40 min apart still block the call, and the message names the reads' own span
        p = self.change_spend(lambda rows: [dict(r, spend="%.2f" % (float(r["spend"]) * 1.35),
                                                 read_at="2026-11-27T%s-05:00" % ("13:05" if r["platform"] == "Google Ads"
                                                                                  else "13:45")) for r in rows])
        result, a = self.run_pulse(overrides={"spend": p, "config": th_cfg, "discounts": None, "inventory": None})
        self.assertEqual(a["mer"]["read_spread_min"], 40)
        self.assertEqual(a["mer"]["call"], "HOLD")
        self.assertTrue(a["mer"]["out_of_step"])
        self.assertIn("spend reads span 1:05 pm–1:45 pm", result.stdout)

    def evening_inputs(self, sales_factor=1.6):
        """6 pm check (as-of 18:05): sample hours 0-13 plus hours 14-17 at last year's sales x sales_factor, and
        every platform read at 18:00 exactly on its expected pace."""
        with (ROOT/"examples/data/hourly.csv").open() as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        with (ROOT/"examples/data/ly_hourly.csv").open() as f:
            ly = {int(r["hour"]): r for r in csv.DictReader(f)}
        for h in range(14, 18):
            r = dict(ly[h], date="2026-11-27")
            r["net_sales"] = "%.2f" % (float(r["net_sales"]) * sales_factor)
            r["orders"] = str(round(int(r["orders"]) * sales_factor))
            rows.append({k: r[k] for k in fields})
        hp = self.folder/"hourly-evening.csv"
        with hp.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        cfg = self.config(as_of="2026-11-27 18:05")
        probe = self.change_spend(lambda rs: [dict(r, read_at="2026-11-27T18:00-05:00") for r in rs])
        _, a = self.run_pulse(overrides={"hourly": hp, "spend": probe, "config": cfg,
                                         "discounts": None, "inventory": None})
        expected = {c["platforms"][0]: c["expected"] for c in a["spend"]["channels"]}
        sp = self.change_spend(lambda rs: [dict(r, read_at="2026-11-27T18:00-05:00",
                                                spend="%.2f" % expected[r["platform"]]) for r in rs])
        return {"hourly": hp, "spend": sp, "config": cfg, "discounts": None, "inventory": None}

    def test_lagging_first_check_does_not_confirm_scale(self):
        # Independent re-check of S-L1: on the first above-scale check the call is HOLD (SCALE needs two checks), so
        # a spend-behind guard that only ran on SCALE let a lagging Meta read (x0.03, MER 9.40) be saved as an
        # above-scale check; the 6 pm check, on pace and above scale, then proposed SCALE. The noon reading must
        # not be saved, so the 6 pm check holds.
        state = self.folder/"state-seq.json"
        state.write_text(json.dumps({"checks": []}))
        result, a = self.run_pulse(overrides={"spend": self.scale_spend("Meta", 0.03)}, extra=["--state", str(state)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(a["mer"]["above_scale"])
        self.assertEqual(a["mer"]["call"], "HOLD")
        self.assertTrue(a["mer"]["spend_behind"])
        self.assertIn("but spend is behind", result.stdout)
        self.assertIn("doesn't count toward SCALE", result.stdout)
        self.assertEqual(json.loads(state.read_text())["checks"], [])
        ev = self.evening_inputs()
        result, a = self.run_pulse(overrides=ev, extra=["--state", str(state)])
        self.assertEqual(result.returncode, 0, result.stderr)
        g = a["mer"]
        self.assertEqual(a["spend"]["status"], "on pace")
        self.assertGreater(g["mer"], g["scale_mer"])         # the 6 pm check itself is above scale
        self.assertEqual(g["call"], "HOLD")
        self.assertNotIn("Raise tomorrow", result.stdout)
        # control: a genuinely above-scale noon check (sales doubled, spend on pace) does confirm the 6 pm SCALE
        state.write_text(json.dumps({"checks": [{"date": "2026-11-27", "time": "12:00", "mer": 6.0, "scale_mer": 5.18}]}))
        result, a = self.run_pulse(overrides=ev, extra=["--state", str(state)])
        self.assertEqual(a["mer"]["call"], "SCALE")

    def test_channel_behind_its_band_blocks_scale(self):
        # Independent re-check: Meta x0.62 (labelled behind) with the total at the edge of the band still proposed
        # SCALE at MER 5.48 (true 4.32). Any budgeted channel behind its pace band blocks SCALE.
        result, a = self.run_pulse(overrides={"spend": self.scale_spend("Meta", 0.62)},
                                   extra=["--state", str(self.prev_above_scale())])
        self.assertEqual(result.returncode, 0, result.stderr)
        g = a["mer"]
        self.assertGreater(g["mer"], g["scale_mer"])
        self.assertEqual(a["spend"]["channels"][0]["status"], "behind")
        self.assertEqual(g["call"], "HOLD")
        self.assertTrue(g["spend_behind"])
        self.assertNotIn("Raise tomorrow", result.stdout)

if __name__ == "__main__":
    unittest.main()
