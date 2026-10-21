# Data contract

UTF-8 CSV with a header. Header names are normalized to lowercase underscores. Monetary inputs must be finite and nonnegative; counts are whole numbers (inventory on_hand may be negative). Use one currency throughout. Blank required numbers are invalid; explicit zero means measured zero.

**Numbers.** Every numeric cell is parsed strictly. Accepted: `1234.56`, `1,234.56`, `$1,234.56`, `€1,234.56` (a currency symbol at either end, commas only as thousands separators). `(50.00)` or `-50.00` is accepted only where negatives are allowed (inventory on_hand). Rejected with exit 2 and a message naming the file, line and column: comma decimals (`2255,74`, `28.320,00`), `nan`, `inf`, exponents (`1e9`), stray text and negatives elsewhere. If more than half of a numeric column is European-format (`1.234,56`), the file is rejected with a specific message. Export with `.` as the decimal separator.

**Net sales** (same definition as the BFCM Holiday Budget Planner and the rest of the BFCM batch) = gross sales − discounts, **before returns/refunds**, excluding tax and shipping.

## Required files

- **plan.csv:** BFCM Holiday Budget Planner daily CSV works unchanged. Minimum: date,total_budget,proj_net_sales_base. Unique YYYY-MM-DD dates; positive base sales. Optional: phase,tag,meta_budget,google_budget,tiktok_budget,other_budget,proj_net_sales_low,proj_net_sales_high,proj_mer_base,ly_date,ly_net_sales,breakeven_mer,target_mer,pull_back_mer,scale_mer,check_times. If channel columns are present, omitted channel amounts become zero; discrepancies with total_budget are noted. low ≤ base ≤ high. check_times uses HH:MM separated by semicolons. pull_back_mer < scale_mer. Next day's row allows a numeric scaling recommendation.
- **hourly.csv:** hour,orders,net_sales; optional date,sessions,sessions_with_add_to_cart,sessions_reached_checkout. One row for every completed hour since midnight, including zeros. At 14:00 or 14:30, hours 0–13 are used; later rows ignored. Hours may be 0–23, HH:00 or dated timestamps. Duplicate/missing hours are errors. Net sales must use the definition above (gross sales − discounts, before returns/refunds, excluding tax and shipping) for today, last year and the plan. Because hourly sales are before returns, an hour can't be negative: a negative net_sales cell exits 2 with that explanation. If your export nets out refunds, re-export without them; do not clip to zero. Shopify's *Net sales* report column is after returns, so use gross sales minus discounts instead.
- **ly_hourly.csv:** same columns, all 24 hours of the chosen comparison date. Must have positive total sales and orders. If the plan omits ly_date, defaults to 364 days earlier; override with --ly-date.
- **spend_today.csv:** platform,spend,read_at; optional date. One cumulative today-to-date read per advertising account/platform, not incremental hourly spend. When a platform has several reads, the latest wins; two rows for one platform with the same read time exit 2 (sum separate accounts into one row before export). Known Meta/Facebook/Instagram aliases map to meta; Google/YouTube to google; TikTok to tiktok; other names to other. Two different names in one channel (for example Meta and Facebook) are both counted and a note warns that the same account may be double-counted.
  - **read_at needs a UTC offset or `Z`** (for example `2026-11-27T13:50-05:00` or `2026-11-27T18:50Z`), converted with config utc_offset. Times without an offset exit 2, because ad platforms often report UTC. If your times are already store time, set `"spend_timestamps_tz": "store"` in config.json deliberately; then `13:50` or `2026-11-27 13:50` are read as store time. read_at must be on the as-of date and no more than 5 minutes after the as-of time.
  - Spend older than stale_spend_minutes blocks budget actions (DATA INCOMPLETE). So does a budgeted channel reporting $0, or less than 2% of its expected spend by its read time, once the first check time has passed, unless that channel is listed in config `paused_channels`.
  - Platform ROAS/attributed revenue columns are ignored. All active ad accounts must be included even when not in the plan.

## Optional files

- **discount_usage.csv:** code,hour,uses,baseline_uses_per_hour. Code is uppercased. One row per code/hour; omitted hours are treated as zero. Baseline must be consistent per code and reflect expected campaign activity. Complete coverage must be verified externally.
- **inventory.csv:** sku,on_hand,sold_last_3h; optional title. Unique SKU. Counts reflect available stock and the three completed hours before the as-of time. No timestamp validation; freshness is the caller's responsibility.
- **config.json:** store,timezone,tz_label,currency_symbol,utc_offset,as_of,quiet_mode,thresholds,paused_channels,spend_timestamps_tz. Unknown keys rejected. paused_channels lists channels paused on purpose (`meta`, `google`, `tiktok`, `other`, or a known platform alias such as `facebook`): they are left out of expected spend, the $0 check and budget actions, and labelled paused. spend_timestamps_tz is `"offset"` (default) or `"store"`. timezone is metadata only. utc_offset is Z or ±HH:MM and must be correct for that day; it converts timestamps carrying offsets into store time. quiet_mode accepts enabled (boolean), summary_times (HH:MM list). CLI --as-of wins over config.
- **state.json:** generated by --state; version 1 and checks array. Do not share across stores. Stores checkpoint date/time, MER, scale/pull-back thresholds, call and actual as_of. Repeated readings replace the current slot. Written atomically, but concurrent writers are unsupported.

## Default thresholds

| Key | Default | Meaning |
|---|---:|---|
| orders_stall_pct | 40 | Latest hour orders below this percent of LY orders × planned revenue growth |
| orders_stall_min_expected | 8 | Minimum expected orders to check |
| checkout_drop_pct | 30 | Relative drop in orders/reached-checkout sessions |
| checkout_baseline_hours | 3 | Earlier hours used for baseline |
| checkout_min_sessions | 20 | Minimum checkout sessions in current hour and average baseline hour |
| aov_drop_pct | 15 | Relative fall versus matching LY hours |
| aov_hours | 2 | Window size |
| aov_min_orders | 20 | Minimum current orders; LY needs 10 |
| code_multiple | 10 | Uses must exceed this multiple of baseline |
| code_min_uses | 25 | Minimum uses; zero baseline also triggers |
| cover_hours | 6 | Stock cover warning threshold |
| resolve_after_hours | 2 | Consecutive clean hours before resolved message |
| max_alerts | 5 | Maximum displayed alerts; JSON retains all |
| spend_pace_band_pct | 20 | Expected spend tolerance, percent |
| stale_spend_minutes | 120 | Maximum age before blocking budget calls |
| min_pace_share_pct | 10 | Minimum LY daily sales share before extrapolation |
| pull_back_cut_pct | 20 | Cut percentage of unspent daily budget |
| max_daily_increase_pct | 30 | Maximum suggested increase over tomorrow's planned budget |
| max_chars | 1800 | Target message length (minimum 300) |

Thresholds must be finite nonnegative numbers; window/count settings must be positive integers. Percentage thresholds should be reviewed for sensible ranges by the operator.

## Outputs

stdout: Slack-ready plain text with Slack-style bold; suppressed by --quiet when appropriate. --json writes full analysis, plan, spend reads, alerts, notes, defaults, checks not performed, quiet decision and message. --state writes local checkpoint history. Exit 0 means analysis completed, not “healthy”; exit 2 means input validation failed. Output I/O failures may raise a Python exception; ensure destination directories exist.

MER cut-off: sales and spend are compared to the same minute, the **earliest spend read time** (capped at the end of the last complete hour). Sales are summed to that minute, pro-rating the hour it falls in evenly; a platform read later than the cut-off has its spend scaled back to the cut-off along last year's hourly sales curve (the same assumption as expected spend). JSON keeps the raw spend total, `spend_at_cutoff`, `sales_at_cutoff` and `cutoff`. A SCALE or PULL BACK call also needs the spend reads within 30 minutes of each other (latest read minus earliest read, `read_spread_min`); otherwise the call is HOLD with "too far apart for a budget call". Reads taken together inside an incomplete hour are not too far apart, even though the cut-off is capped at the end of the last complete hour. Whenever MER is above the scale line (a SCALE call, or a first above-scale check that would otherwise be saved as the confirming check for the next one), the call is HOLD while spend is behind: total spend below its `spend_pace_band_pct` band, or any budgeted, unpaused channel below its own band by its read time. JSON sets `spend_behind` and `spend_behind_reasons` (and `blocked_call` when the call would have been SCALE), the message says "spend is behind" and that the check doesn't count toward SCALE, and the reading is not saved to state, since an under-read spend figure inflates MER (scaled and raw alike). Because scaling back can only remove spend (and so only raise MER), a SCALE or PULL BACK also needs the raw MER, sales to the cut-off over every platform's spend as read, to cross the same line; if it does not, the call is HOLD ("the two disagree") and JSON sets `raw_disagrees` with `mer_raw` and `blocked_call`. With no sales by the cut-off, MER is n/a (call NO SALES); with no spend, NO SPEND.

End-of-day pace = completed sales / LY share through those hours. Expected spend uses the LY sales curve interpolated to each channel's read time. Budget calls compare MER rounded to two decimals. SCALE means both adjacent checkpoints exceeded their own scale thresholds; missing previous state yields HOLD. SCALE and PULL BACK lines are printed as "Proposed (needs approval):".

Not handled: on the DST change day (Nov 1) hours are 0–23 with no duplicate hour, so a 25-hour export must be merged to 24 rows before use.
