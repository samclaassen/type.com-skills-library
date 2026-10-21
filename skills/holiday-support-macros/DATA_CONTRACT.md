# Data contract: holiday-support-macros

Two required files (this season's policies and last season's tickets) and three optional ones. UTF-8 (a BOM is fine). CSV files have a header row; column order doesn't matter, header case and `_`/space are ignored, and unknown columns are ignored. Any validation failure exits 2 with the file, line or field, and the reason.

## 1. Holiday policies (`--policies`, JSON, required)

What the store has written down for this season. **Every fact in a generated macro comes from here.** A missing section or field is never filled in: the macros that need it are not generated and the report names the field. `null` counts as missing. Unknown keys (for example `"_note"`) are ignored. Dates are `YYYY-MM-DD`; numbers are 0 or more.

| Key | Type | Notes |
| --- | --- | --- |
| `store` | text, **required** | Printed in the report title. |
| `macros_live_by` | date | When the macros must be live. Default: Black Friday − 7 days (Nov 20 in 2026). Policy decisions are due 7 days before it, draft review 3 days before it. |

**`shipping`**: answers WISMO and shipping-deadline tickets.

| Key | Type | Notes |
| --- | --- | --- |
| `services` | list of `{name, last_order_date, checkout_rate}` | One entry per carrier service, with its last safe order date for delivery by `deliver_by`. `checkout_rate` is the shipping option customers pick at checkout (e.g. `Standard`); give it on every entry or none. With it, customer macros list one date per checkout rate, the **earliest** of its carriers' dates, and never a carrier's own date (customers can't pick the carrier). Without it, each service is treated as an option customers pick. Take these from the Holiday Shipping Cutoff Checker, or pass its result with `--cutoffs` (section 7). Each date must be in this season's year and not after `deliver_by`. |
| `deliver_by` | date | The date the cutoffs aim for, e.g. `2026-12-24`. |
| `delivery_coverage_pct` | number, above 0 and at most 100 | The share of customers the dates hold for (the Cutoff Checker's threshold, e.g. `95`). Printed as "These dates hold for most addresses (95% of our customers)." |
| `same_day_cutoff` | text | e.g. `1 pm ET`: orders placed by then on a business day ship the same day. |
| `closed_dates` | list of dates | Days the warehouse doesn't ship. |
| `ships_from`, `ship_days`, `source` | text | For your records (`source`: where the dates came from). |

**`bfcm_backlog`**: how long BFCM orders take to ship. If it's missing (and no `--cutoffs` file supplies it), the BFCM-backlog WISMO macro is not generated and the report warns, because the normal WISMO macro promises same-day shipping. A `--cutoffs` file's computed backlog replaces it when they disagree (section 7).

| Key | Type | Notes |
| --- | --- | --- |
| `orders_from`, `orders_to` | date, required | Orders placed in this range are in the backlog. |
| `ships_within_business_days` | whole number, required | |
| `clears_by` | date, required | When you expect to be caught up. |

**`returns`**

| Key | Type | Notes |
| --- | --- | --- |
| `standard_days` | whole number | Return window in days. |
| `counted_from` | `delivery` or `order` | Printed as "30 days of delivery". Leave out if your policy doesn't say. |
| `holiday_window` | `{orders_from, orders_to, return_by}` | Holiday extension: orders placed in the range can be returned until `return_by`. With it, the holiday returns macro replaces the standard one, and any existing returns macro that doesn't mention `return_by` is flagged. |
| `condition` | text | e.g. `unused and in the original packaging`. |
| `start_url` | text | Where to start a return. |
| `refund_to` | text | e.g. `your original payment method`. |
| `return_label_fee` | number | 0 = free return shipping. |
| `exchanges` | true/false | |
| `final_sale` | list of text | Collections or items that can't be returned. |
| `gift_returns` | text | Your rule for gift recipients, written for customers. |

**`price_adjustment`**: often missing, and a common holiday gap. `null` or absent = no policy.

| Key | Type | Notes |
| --- | --- | --- |
| `offered` | true/false, required | `false` generates a polite "we don't adjust prices" macro. |
| `window_days` | whole number | Required when `offered` is true. |
| `how`, `alternative` | text | How to ask; what you offer instead (e.g. return and reorder within the return window). |
| `excludes` | list of text | |

**`discounts`**

| Key | Type | Notes |
| --- | --- | --- |
| `one_code_per_order` | true/false | |
| `codes_combine_with_sale` | true/false | Whether codes stack with the sale. |
| `free_shipping_threshold` | number | e.g. `75` for $75. |
| `free_shipping_combines` | true/false | Whether free shipping still applies with a code or the sale. |
| `sale` | `{name, starts, ends, automatic, excludes}` | `name` is required inside it. `excludes` are collection names. |
| `codes` | list of `{code, offer, starts, ends}` | **This season's** valid codes. Existing macros that mention any other code are flagged. Take them from BFCM Offer & Discount Config QA once its fixes are in. |

**`order_changes`**: `window_minutes` (whole number: how long after ordering a customer can change or cancel), `how` (text).
**`address_change`**: `until` (text, e.g. `your order ships`), `after_shipping` (text).
**`gift_options`**: `gift_message`, `gift_receipt`, `gift_receipt_hides_prices`, `gift_wrap`, `ship_to_another_address` (true/false), `gift_wrap_price` (number), `e_gift_cards` (text, written for customers). "Gift receipts don't show prices" is only said when `gift_receipt_hides_prices` is true.
**`out_of_stock`**: `backorders`, `restock_alerts` (true/false).
**`damaged_defective`**: `report_within_days` (whole number), `photo_required`, `return_required` (true/false), `resolution` (text, e.g. `a free replacement, or a full refund if it's sold out`).
**`international`**: `ships_to` (text, e.g. `the United States only, including APO/FPO addresses`).
**`support_hours`**: `regular`, `holiday_note` (text), `closed_dates` (dates; support is closed, so capacity is 0 that day), `first_reply_hours` (whole number), `weekend_coverage` (`{from, to}` dates: weekends inside it get `--weekend-agents`; weekends outside it count as closed. Without it, every weekend gets `--weekend-agents`).
**`escalation`**: `order_value_over`, `refund_over` (numbers), `owner` (text, who gets escalations), `keywords` (list of words or phrases; last season's tickets containing them are counted and flagged `escalate` in `tickets_classified.csv`). `refund_over` and `owner` also go into the damaged-item macro ("If that's worth more than $150, the support lead approves it first").

**Validation** (exit 2): wrong types, missing required keys, dates out of order (backlog, holiday window, sale, weekend coverage), a service listed twice, `checkout_rate` on some services but not others, a code listed twice or containing spaces, `price_adjustment.offered: true` without `window_days`, and **dates from another season** (shipping, backlog, holiday window and sale dates must be in the season the tickets point to: tickets from 2025 mean the 2026 season).

## 2. Last season's tickets (`--tickets`, CSV, required)

One row per ticket from last holiday season, as exported from your helpdesk (Gorgias, Zendesk, Help Scout, Shopify Inbox and others). Export menus change; check your helpdesk's help center for the current CSV export. Up to 6 months of tickets (for example Nov 1–Jan 31).

| Column | Required | Also accepted | Notes |
| --- | --- | --- | --- |
| `created_at` | yes | `created`, `created date`, `created datetime`, `date`, `requested`, `created on` | Starts with `YYYY-MM-DD` (ISO, with or without a time) or `MM/DD/YYYY` (`DD/MM/YYYY` with `--date-format dmy`). If every slash date in the file could be read either way (both parts 12 or less), the run stops and asks for `--date-format mdy` or `dmy`. The date part is used as written (a `Z` or offset is not converted), so export in the store's time zone if you can. |
| `subject` | one of the two | `title` | |
| `body` | one of the two | `message`, `first message`, `description`, `preview`, `text`, `content` | The customer's first message. A short preview is enough. |
| `id` | no | `ticket id`, `ticket`, `conversation id`, `conversation`, `number` | Must be unique when present. |
| `channel` | no | `via`, `source`, `type` | Kept in the classified-tickets file. |
| `tags` | no | `tag`, `labels` | Separated by `,` `;` or `|`. Rules can match tags. |

More than 100,000 tickets prints a warning on stderr (one season is enough; very large files take minutes and hundreds of MB).

The season is taken from the tickets: tickets from Nov–Dec 2025 make 2026 "this season", and its forecast covers the same days 364 days later (same weekdays, so Black Friday lines up with Black Friday).

## 3. Existing macros (`--macros`, CSV, optional)

Your current macros or saved replies: `name` (also `title`, `macro`), `body` (also `text`, `content`, `message`, `response`), optional `tags`. Placeholders in any helpdesk syntax (`{{…}}`, `{%…%}`) are ignored by the checks.

Checks: a date with a year that has passed; a date next to delivery words (order by, arrive, deliver, Christmas, cutoff, in time) that isn't one of this season's last order dates, `deliver_by` or backlog dates; a date next to return words that isn't the holiday window's; a year earlier than this season on its own; numbers against the policy, with the context word before the number (return window "within N days" after a return word but not refund-processing times, damage-report window, change/cancel window, reply time; the free-shipping `$` amount anywhere in a free-shipping sentence); a returns macro that states a window but not the holiday `return_by` date; code-like words (an all-caps word of 4+ characters with a digit, like `BF20`, or an all-caps word right after "code", "coupon" or "promo", like `FREESHIP`) that aren't in `discounts.codes`; and delivery promises ("guarantee", "promise", "definitely", "100%"…) unless negated ("we can't promise").

Once your new macros are live, export them and pass that export here: a generated macro whose name is found is **applied**, and **verified** when it has no problems, still contains every date and number of the draft, and needs no placeholder filled by hand. It is **needs manual fill** instead when a generic placeholder (`{{order_number}}`…) is still in the live copy, or when `--helpdesk` names a helpdesk with no variable for a placeholder the macro uses (`help-scout`: order number and tracking link; `shopify-inbox`: all four). `gorgias` and `zendesk` can fill all four once mapped (order number and tracking link through the Shopify integration or a custom field: verify in your helpdesk).

## 4. Orders (`--orders`, optional, repeatable)

Daily orders, for tickets per 100 orders and to scale the forecast.

- **CSV:** `date` (`YYYY-MM-DD`), `orders` (also `total orders`, `order count`, `projected orders`; a number such as `1234`, `1,234` or `12.5`; anything else, including `nan`, exits 2). Other columns are ignored. Put last season's days and this season's projection in one file or in two (`--orders a.csv --orders b.csv`). A date may appear only once across all files.
- **BFCM Holiday Budget Planner `--json` result:** its `rows[].date` and `rows[].orders` are read as this season's projection. The planner's `--csv` plan has net sales but no orders column, so it is refused with a pointer to the `--json` plan.

**Using the Budget Planner's plan:** pass last season's daily orders (Shopify Analytics) **and** the planner's `--json` plan: `--orders last_season.csv --orders plan.json`. The plan alone isn't enough (it has no last-season days). If your orders file also has this season's projection rows, remove them, or add `--prefer-plan`: the plan's orders then replace the CSV's on the same dates and the report says how many days were replaced.

Needed: every day of last season that the tickets cover (exit 2 otherwise). This season's days without a projection use last season's same-weekday orders × the growth of the days that have one (or `--growth`), and the report says which days. Without `--orders`, the forecast is last season's tickets × `--growth` (default 1.00) and tickets per 100 orders are not shown.

**Forecast.** For this season's day D and ticket type T: last season's tickets of type T on D − 364 days × (this season's orders over the 7 days up to D ÷ last season's orders over the aligned 7 days). The window starts at the first day of the season, so early days use fewer days.

## 5. Facts available to templates

Templates (section 6) use these as `[[fact]]`. Each comes only from the policy fields shown; when they are missing, the fact is missing.

| Fact | From | Example (sample store) |
| --- | --- | --- |
| `same_day_cutoff`, `closed_dates` | `shipping.same_day_cutoff`, `shipping.closed_dates` | `1 pm ET`; `Thursday, November 26 and Friday, December 25` |
| `same_day_rule` | `shipping.same_day_cutoff` plus `bfcm_backlog` (or the Cutoff Checker's backlog) | Without a backlog: `Orders placed by 1 pm ET on a business day ship the same day`. With one, the same-day promise is qualified with the backlog window, its business days and the catch-up date, and same-day shipping is only promised again from that date. Templates use this fact, never `same_day_cutoff` on its own, for shipping-time promises. |
| `deliver_by` | `shipping.deliver_by` | `Thursday, December 24` |
| `cutoff_lines` | `shipping.services` | `- Standard: order by Monday, December 14` (one line per checkout rate, its earliest carrier date; per service when no `checkout_rate` is given) |
| `checkout_options` | `shipping.services` | `Standard or Express` |
| `last_service`, `last_cutoff`, `earlier_services` | `shipping.services` | `Express`; `Monday, December 21`; `Standard` (not applicable with one option) |
| `delivery_coverage` | `shipping.delivery_coverage_pct` | `95%` |
| `backlog_orders`, `backlog_ship_days`, `backlog_clears` | `bfcm_backlog` | `from Thursday, November 26 to Monday, November 30`; `3 business days`; `Thursday, December 3` |
| `return_window` | `returns.standard_days`, `counted_from` | `30 days of delivery` |
| `has_holiday_window`, `holiday_from`, `holiday_to`, `holiday_return_by` | `returns.holiday_window` | yes; `Sunday, November 1`; `Thursday, December 24`; `Sunday, January 31, 2027` |
| `return_condition`, `return_start`, `refund_to`, `gift_returns` | `returns.*` | as written |
| `refund_rule` | `returns.refund_to` | `Refunds go back to your original payment method.` |
| `return_shipping`, `exchange_rule`, `final_sale` | `returns.return_label_fee`, `exchanges`, `final_sale` | `Return shipping is free.`; `Items from Last Chance and Gift Cards are final sale.` |
| `pa_offered`, `pa_window`, `pa_how`, `pa_excludes`, `pa_alternative` | `price_adjustment.*` | missing in the sample |
| `one_code_rule`, `sale_stack_rule` | `discounts.one_code_per_order`, `codes_combine_with_sale` | `Only one discount code can be used per order.` |
| `sale_name`, `sale_dates`, `sale_auto_rule`, `sale_excludes` | `discounts.sale` | `BFCM sale (25% off sitewide)`; `from Thursday, November 26 to Tuesday, December 1` |
| `free_shipping_rule` | `discounts.free_shipping_threshold`, `free_shipping_combines` | `Free shipping on orders of $75 or more still applies on top of a code or the sale.` |
| `edit_window`, `edit_how` | `order_changes.*` | `1 hour` |
| `address_until`, `address_after` | `address_change.*` | `your order ships` |
| `gift_message_rule`, `gift_receipt_rule`, `gift_wrap_rule`, `ship_to_other_rule`, `e_gift_cards` | `gift_options.*` | `We don't offer gift wrapping.` |
| `backorder_rule`, `restock_rule` | `out_of_stock.*` | |
| `damage_days`, `damage_photo`, `damage_resolution`, `damage_return` | `damaged_defective.*` | `14 days` |
| `refund_over`, `escalation_owner` | `escalation.refund_over`, `escalation.owner` | `$150`; `the support lead` |
| `ships_to` | `international.ships_to` | |
| `support_regular`, `support_holiday_note`, `support_closed`, `first_reply` | `support_hours.*` | `24 hours` |

Yes/no fields become fixed sentences (for example `gift_wrap: false` → "We don't offer gift wrapping."). Dates are written out with the weekday; a date outside the season's year gets its year. Date facts for `use_from` / `use_until`: `backlog_from_date`, `backlog_clears_date`, `first_cutoff_date`, `before_last_cutoff_date`, `last_cutoff_date`, `deliver_by_date`, `holiday_return_by_date`, `sale_start_date`, `sale_end_date`.

## 6. Rules and templates (`--rules`, `--templates`, JSON, optional)

Defaults: `references/ticket_rules.json` and `references/macro_templates.json`. Edit copies and pass them with these flags.

**Rules:** `{"fallback": "other", "types": [{"type", "label", "keywords", "first", "not", "tags", "negation", "escalate"}]}`. Case-insensitive whole-word or phrase match on subject + body (curly quotes are normalised). Order of checks: the keywords of types with `"escalate": true` (they go to a person, never a macro), then every type's `first` phrases, then each type's `keywords` and `tags` in list order; the first match wins. Phrases in `not` are removed before that type's phrases are matched (so `zip code` isn't `code`). A phrase doesn't count when `not`, `no`, `isn't`, `wasn't`, `never`, `aren't` or `weren't` is among the 3 words before it, unless the type has `"negation": false`. `type` is a lowercase slug, each type appears once, and `keywords` can't be empty.

**Templates:** `{"templates": [{"id", "name", "ticket_type", "tags", "body", "when", "requires_any", "if_missing", "replaces", "use_from", "use_until"}]}`.
- `body` is a list of lines. `{{customer_first_name}}`, `{{order_number}}`, `{{tracking_url}}` and `{{agent_first_name}}` are the only placeholders; they are kept for the helpdesk. `[[fact]]` is filled from section 5.
- A missing fact on a normal line: the macro is not generated. A line starting with `?` is optional and is dropped instead.
- `when`: `"fact"` or `"!fact"` (a yes/no fact must be true or false). `requires_any`: at least one listed fact must exist.
- `if_missing: "warning"` makes a missing policy a warning when the ticket type is at least 3% of tickets (default: noted only).
- `replaces`: words matched on old macros' names to suggest this macro as the replacement (otherwise the old macro's ticket type is used).
- `use_from` / `use_until`: a date fact, optionally `+N` / `-N` days, or a list of them (first available wins). They drive the "Use" column and the switch-over dates in the checklist.
- **Refused (exit 2):** a name or line with digits, `$`, `%`, `€`, `£` or a month name outside `{{ }}` and `[[ ]]`; a body line with a promise word outside `[[ ]]` (`guarantee(d)`, `refund…`, `free`, `always`, `never`, `Christmas`, `holiday…`, `arrive by`, `in full`): a promise must come from a policy fact; an unknown fact or placeholder; or a `ticket_type` that isn't in the rules.

Macro names become `Holiday <season> - <name>` and tags `holiday-<season>`, the ticket type (`general` for `other`) and the template's tags.

## 7. Shipping cutoffs (`--cutoffs`, JSON, optional)

The Holiday Shipping Cutoff Checker's `--json` result, passed unchanged. Read strictly: a missing field, a field this skill doesn't know, or values that don't agree with each other exit 2 rather than being dropped.

| Field | Used for |
| --- | --- |
| `target_date` | `shipping.deliver_by` |
| `threshold_pct` | `shipping.delivery_coverage_pct` ("for 95% of customers") |
| `services[]`: `name` (full name such as `UPS Ground`; if absent, `carrier` + `service`), `checkout_rate`, `last_safe_order_date` | `shipping.services`. A `null` date means no safe date: the service is left out and the report says so |
| `checkout_rates[]`: `rate`, `services`, `last_safe_order_date`, `extend_with`, `extend_to` | Checked: every service sits under exactly one rate, and each rate's date is the earliest of its services' dates |
| `backlog`: `modeled`, `bfcm_window` `[from, to]`, `bfcm_ship_within_business_days`, `bfcm_clears` | `bfcm_backlog` (`orders_from`, `orders_to`, `ships_within_business_days`, `clears_by`) |
| `cutoff.label` | `shipping.same_day_cutoff`. When policies.json gives a different time (compared after normalising, so `1 pm ET` and `1:00 pm ET` agree), the macros use the checker's time and the report warns with both values |
| `account`, `all_cutoffs_passed`, `as_of`, `calendar`, `counts`, `coverage_by_order_date`, `fixes`, `issues`, `promises`, `verdict`, `zone_mix`, `services[].{carrier, service, last_safe_by_threshold, transit_last_safe, ship_by_date, basis, needs_cutoff_time, peak_extra_days, transit_source, ship_by_source}`, `backlog.{bfcm_orders_forecast, periods, ship_days}` | Known but not used (`as_of` is printed) |

**The checker's values win.** They are computed from your carriers, order forecast and warehouse capacity. When policies.json also gives the last order dates (compared per checkout rate), `deliver_by`, the coverage, the same-day cutoff time or the BFCM backlog, and they disagree, the macros use the checker's values and the report prints a **WARNING** with both values, so a macro never promises an earlier clear date than the checker computed. The backlog:
- `modeled` with `bfcm_clears` and ship-within days: used, replacing `bfcm_backlog` (or supplying it when policies.json has none, so the BFCM backlog macro is still generated).
- `bfcm_clears` without ship-within days: a policy `clears_by` earlier than `bfcm_clears` is moved to it (with a warning).
- `modeled` but no `bfcm_clears` (no BFCM backlog) or not modeled (the checker ran without a demand file): `bfcm_backlog` from policies.json is kept, and the report says why.

Run the checker with the Budget Planner's `--csv` plan as demand and `--aov` set to the planner's AOV (88.66 in the sample), or its order projection, and so its backlog, will differ.

## 8. Capacity (flags, optional)

`--agents N` (agents on shift Monday–Friday), `--weekend-agents N` (default: `--agents`; only inside `support_hours.weekend_coverage` when it is set), `--tickets-per-agent N` (tickets one agent handles in a shift; take it from your helpdesk's productivity report), `--shift-hours H` (default 8). Days in `support_hours.closed_dates`, and weekends outside `support_hours.weekend_coverage`, have no capacity.

Per day: workload = forecast + tickets still waiting from the day before (with `--agents`); agents needed = workload ÷ tickets per agent, rounded up; agent-hours = workload × shift hours ÷ tickets per agent; capacity = agents on shift × tickets per agent; short by = forecast − capacity. Unanswered tickets carry over: waiting = max(0, yesterday's waiting + forecast − capacity).

## 9. Outputs

- **stdout:** the Markdown report.
- **`--out-dir DIR`:** `macros.csv` (`name, tags, body`; tags comma-separated in one cell; body with line breaks), `macros_preview.md` (each macro with its type, tags, dates to use it and the policy fields it came from, plus the placeholder table), `forecast.csv` (one row per day: date, weekday, aligned last-season date, orders, tickets by type, agents needed, agent-hours, capacity, short by, waiting at day end), `tickets_classified.csv` (`id, date, type, escalate, matched, subject, body`; `escalate` is `yes` for escalation-type tickets and tickets containing an `escalation.keywords` word; `matched` is the keyword or `tag:…` that decided the type). The last file holds the **raw ticket text** (names, emails, addresses as the customer wrote them): keep it local and delete it when done. Cells starting with `=`, `+`, `-` or `@` get a leading `'` so a spreadsheet won't run them as formulas.
- **Report examples** (most common unclassified subjects) mask emails, phone numbers, street addresses, names after "my name is" / "this is" / a sign-off, and numbers of 4+ digits.
- **`--json PATH`:** the analysis, machine-readable.
- **`--as-of YYYY-MM-DD`:** the report date (default: today). Dates that have passed print as "now (… has passed)".

## 10. Verdict and dates

- **NOT READY**: at least one critical issue: a ticket type with ≥ 3% of last season's tickets and no policy to answer it (no macro could be generated), or an existing macro with a date that has passed or a date or number that contradicts the policy.
- **FIX BEFORE <live-by date>**: warnings only: policies.json disagreeing with the `--cutoffs` file, an old code, a delivery promise, a returns macro without the holiday window, a year on its own, a macro marked `if_missing: warning` that couldn't be generated, forecast above capacity, or more than 5% of tickets unclassified.
- **READY**: none of these.

Dates: Black Friday is the day after the fourth Thursday of November. Macros live by Black Friday − 7 (or `macros_live_by`); policy decisions 7 days before that; draft review 3 days before that; critical fixes to existing macros within 7 days of the run; final re-run Black Friday − 3.
