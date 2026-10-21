# Holiday Support Macros: Playbook

Every change in this playbook needs the user's explicit approval. Nothing here is done by the script: it reads files and writes drafts. After a change is made, read it back, export the macros and re-run the skill.

## 1. Tuning the ticket rules

The rules live in `references/ticket_rules.json`. Matching is on whole words and phrases in the subject and first message ("broke" doesn't match "broken"). Escalation types (`"escalate": true`: chargebacks, legal threats, injuries) are checked first, then every type's `first` phrases (for example WISMO's "hasn't arrived", so "my package hasn't arrived, can I get a refund?" is WISMO, not a return), then the keywords in list order: the first type that matches wins. A keyword doesn't count when "not", "no", "isn't", "wasn't" or "never" comes up to 3 words before it ("the box was not damaged"); WISMO turns this off with `"negation": false` because "no tracking" is a WISMO ticket.

1. Look at "Most common" under **Unclassified** in the report, and open `tickets_classified.csv` (from `--out-dir`) filtered to `other`.
2. If a subject clearly belongs to a type, add its phrase to that type's `keywords` (for example `haven't received` and `any update` under `wismo`). Prefer phrases to single words.
3. If a keyword catches the wrong tickets, add the phrase that causes it to that type's `not` list (`zip code` under `discount_code` stops "zip code" counting as "code"; `return customer` under `return_exchange`; `order by phone` under `shipping_deadline`).
4. Keep specific types above general ones. WISMO is last because "order" and "package" appear in almost every ticket. Damaged sits above returns, so "arrived broken, want a refund" is a damage claim, not a return.
5. If your helpdesk already tags tickets (Gorgias rules, Zendesk triggers), add those tags to the type's `tags` list.
6. Re-run and check the `matched` column. Aim for under 5% unclassified. Above that, the report warns that the mix and forecast are incomplete. The rules are in English: if many tickets are in another language, add keywords in that language.
7. The `escalate` column of `tickets_classified.csv` is `yes` for escalation-type tickets and any ticket containing a word from `escalation.keywords` in policies.json.

Add a type of your own (for example `subscription`) the same way. Policy-gap checks and macros only apply to types that have templates.

## 2. Forecast and staffing

**How the forecast works.** Each day of this season is matched to the same weekday last season (364 days earlier, so Black Friday lines up with Black Friday). That day's tickets, type by type, are scaled by this season's projected orders ÷ last season's orders over the 7 days up to that day. A week of orders drives a week of WISMO, damage and return tickets, so the shape of last season (the post-Black-Friday WISMO wave, the shipping-deadline peak before the cutoffs, the post-Christmas returns) carries over at this season's size.

**What it doesn't know.** A new policy (for example a published price-adjustment rule) changes how many customers ask. A shipping problem this year that didn't happen last year. Fixed-date holidays shift by one weekday (last year's Christmas Day lines up with this year's Dec 24). Read the forecast as a planning range, not a promise.

**Capacity.** Agents needed and agent-hours cover the day's forecast plus the tickets still waiting from the day before. If you only staff weekends for part of the season, put the dates in `support_hours.weekend_coverage` (`{"from", "to"}`): weekends outside it count as closed. Tickets per agent per shift is the number that matters most. Take it from your helpdesk's productivity report for last December, or count closed tickets per agent-day. With good macros, simple WISMO tickets go faster; returns and damage claims slower.

**When the report says you're short:**
- Weekends right after Black Friday are the usual gap: one agent faces the Thanksgiving and Black Friday backlog plus the weekend's own tickets.
- Days support is closed (Thanksgiving, Christmas) still receive tickets. Decide who clears them the next morning, and set an auto-reply with the holiday support hours macro.
- Options: a second agent on the short days, longer shifts, a trained teammate from operations or marketing for WISMO and shipping questions, an outsourced team for overflow, or a published response time ("we reply within 24 hours") so customers know what to expect.
- Every WISMO ticket avoided is capacity gained: a tracking link in the shipping confirmation email, an order-status page, and the shipping dates on the site all cut WISMO.

## 3. Closing policy gaps

For each gap, decide, write it on the store's FAQ or policy page, add it to `policies.json`, and re-run. Options below; the owner chooses.

### Price adjustments ("I bought it last week, now it's 25% off")

| Option | What to write | Trade-off |
| --- | --- | --- |
| No adjustments | `"price_adjustment": {"offered": false, "alternative": "…"}` | Simplest and cheapest. Expect some returns-and-rebuys, which cost shipping both ways. |
| No adjustments, but return and reorder | `offered: false`, `alternative`: "You're welcome to return it under our return policy and order again at the sale price." | Honest and consistent; the customer does the work. |
| Adjust within N days of the order | `{"offered": true, "window_days": 14, "how": "…", "excludes": ["Last Chance"]}` | Goodwill and fewer returns, but every pre-sale buyer in the window will ask. Size it: last season's price-adjustment tickets × the average discount. |
| Store credit instead of a refund | `offered: true` with `how`: "We'll send the difference as store credit." | Keeps the money in the store. |

Whatever you choose, apply it to everyone: two customers comparing answers is how these tickets become public complaints. Until it's decided, route these tickets to the owner (escalation map).

### Other common holiday gaps

- **BFCM backlog note** (`bfcm_backlog`): if orders take longer than usual to ship over cyber week, say how long and when you'll be caught up. Without it, the WISMO macro promises same-day shipping during the busiest week. Use the Holiday Shipping Cutoff Checker's backlog estimate.
- **Holiday returns window** (`returns.holiday_window`): gifts bought in November can otherwise fall out of a 30-day window before they're opened. Common choice: orders from Nov 1 to Dec 24 returnable until the end of January.
- **Change/cancel window** (`order_changes.window_minutes`): how long before the order goes to the warehouse. Over BFCM, a short, fixed window is easier to keep than "as soon as possible".
- **Address changes** (`address_change`): until when, and what happens after shipping.
- **Gift options** (`gift_options`): gift messages, gift receipts or hidden prices, gift wrap, shipping to another address, e-gift cards for last-minute buyers.
- **Backorders and restock alerts** (`out_of_stock`).
- **Damaged items** (`damaged_defective`): how many days to report, whether a photo is needed, replacement or refund, whether the item comes back.
- **International** (`international.ships_to`).
- **Holiday support hours** (`support_hours`): days closed, weekend cover, reply time.

## 4. Helpdesk placeholders

The macros use four generic placeholders. Replace them with your helpdesk's own syntax before saving. "Confirmed" means the syntax was shown in the helpdesk's own documentation when this skill was written (Oct 7, 2026). Everything else says **verify in your helpdesk**: check it against a test ticket before using it.

| Placeholder | Gorgias | Zendesk | Help Scout (saved replies) | Shopify Inbox (quick replies) |
| --- | --- | --- | --- | --- |
| `{{customer_first_name}}` | `{{ticket.customer.firstname}}` (confirmed) | `{{ticket.requester.first_name}}` (confirmed) | `{%customer.firstName,fallback=there%}` (confirmed) | No variables documented: type it in |
| `{{agent_first_name}}` | `{{current_user.firstname}}` (confirmed) | `{{current_user.first_name}}` (confirmed) | `{%user.firstName%}` (confirmed) | Type it in |
| `{{order_number}}` | Not in Gorgias' list (`orders[0].id` is Shopify's internal ID, not the order number). Commonly `{{ticket.customer.integrations.shopify.orders[0].name}}`: verify in your helpdesk | No built-in placeholder. A custom ticket field `{{ticket.ticket_field_<field ID>}}` filled by your Shopify app: verify in your helpdesk | No variable: type it in | Type it in |
| `{{tracking_url}}` | Not in Gorgias' list. Commonly `{{ticket.customer.integrations.shopify.orders[0].fulfillments[0].tracking_url}}`: verify in your helpdesk | Custom ticket field, as above: verify in your helpdesk | No variable: type it in | Type it in |

Sources:
- Gorgias, "Use variables in macros": https://helpcenter.gorgias.com/en-US/macro-variables-101-81845. Macros are created from the top-left menu → Workflows → Tools → Macros → Create macro. A variable that can't be filled for a ticket becomes a blank, and `orders[0]` is the customer's most recent order, which may not be the one the ticket is about.
- Zendesk, "Placeholder reference for business rules": https://support.zendesk.com/hc/en-us/articles/4408886858138-Zendesk-Support-placeholders-reference. `current_user` is the person updating the ticket; custom fields use `{{ticket.ticket_field_<field ID>}}`.
- Help Scout, "Work With Variables": https://docs.helpscout.com/article/468-work-with-variables. `customer.firstName` and `user.firstName` work in saved replies; the `fallback=` attribute fills in text when the field is empty.
- Shopify Inbox, "Quick replies": https://help.shopify.com/en/manual/inbox/configure-inbox/quick-replies. A quick reply is a one-word shortcut and a message; no variables are documented, so replace the placeholders by hand. Inbox is chat: keep those versions short.
- Any other helpdesk (Freshdesk, Re:amaze, Richpanel, Gladly…): verify in your helpdesk.

## 5. Brand voice without changing facts

Change the wording, never the facts. Before (draft) and after (Fernhill's warmer voice), with every fact kept:

> Draft: "Here are the last days to order for delivery by Thursday, December 24: … Orders placed by 1 pm ET on a business day ship the same day."
>
> In voice: "Want it under the tree? Order by these dates for delivery by Thursday, December 24: … Anything ordered by 1 pm ET on a business day leaves our Columbus warehouse the same day."

Allowed: greeting, sign-off, tone, sentence order, a short brand detail that is in your policies ("our Columbus warehouse" comes from `shipping.ships_from`). Not allowed: "on a weekday" for "on a business day" (Thanksgiving is a weekday the warehouse is closed), "Order by the 21st and it's guaranteed" (a promise), "about two weeks" instead of the dates, dropping a shipping method, rounding "$75" to "$70-ish", or "returns are easy" without the window.

Check: export the edited macros and re-run with `--macros` (and `--helpdesk`). A changed or missing date or number turns the macro from **verified** to **applied** and is listed. A macro that still has a generic `{{…}}` placeholder, or one your helpdesk has no variable for (order number and tracking link on Help Scout and Shopify Inbox), is **needs manual fill**, never verified.

## 6. Escalation map

| Situation | Who | How fast | Macro? |
| --- | --- | --- | --- |
| Order above `order_value_over`, or a refund/credit/replacement above `refund_over` | owner (`escalation.owner`) | same day | No: a person approves the amount |
| Chargeback, payment dispute, suspected fraud | owner | same day | No |
| Injury, burn, allergic reaction, broken glass, any safety issue | owner | same day | No: ask for photos, keep them, don't argue liability |
| Legal threat, press, influencer complaint | owner | same day | No |
| A ticket type with a policy gap | owner | within the reply-time promise | No, until the policy is decided |
| Third message about the same order | senior agent or owner | next reply | No: read the thread, fix the cause |

Internal note to hand over a ticket: "Escalating: [situation]. Order [number], value [amount]. Customer asks: [one line]. What we've said so far: [one line]. Needs: [decision] by [time]."

## 7. Timeline for BFCM 2026 (Black Friday Nov 27)

| When | What |
| --- | --- |
| Within a week of the first run | Retire or correct CRITICAL existing macros (they may be in use now). |
| Nov 13 (live-by − 7) | Policy gaps decided and published. |
| Nov 17 (live-by − 3) | Drafts reviewed: facts, tone, placeholders mapped. WARNING macros fixed. |
| Nov 20 (Black Friday − 7) | Macros live and tagged; staffing for the short days confirmed; re-run with the macros export. |
| Nov 24 (Black Friday − 3) | Final re-run with the final policies and cutoff dates. Brief the team on the escalation map. |
| Nov 26 → backlog clears | Use the BFCM-backlog WISMO macro, then switch back. |
| Cutoff dates | Switch the shipping-deadline macros as each date passes. |
| End of the holiday returns window | Retire the holiday returns macro. |

## 8. Status update template

> **Holiday support readiness, [store], [date]**: [verdict].
> Last season: [N] tickets ([mix: WISMO x%, …]). This season's forecast: [N], busiest [day] ([n]).
> Decisions needed: [gaps, with decide-by dates].
> Old macros to fix: [names and what's wrong].
> Staffing: short on [days]; plan: [cover].
> New macros: [n] drafts ready for review by [date]; live by [date].
> Nothing has been changed in the helpdesk or store.
