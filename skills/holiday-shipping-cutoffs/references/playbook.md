# Holiday Shipping Cutoff Checker: Playbook

Every change in this playbook needs the user's explicit approval. After a change, look at the live page (or send a test email or SMS), then re-run the checker. Menu labels move; if a path below doesn't match, look for the named page or setting. Links were checked on Oct 7, 2026; carriers move their holiday pages every year, so always record the exact URL you used in `source`.

## 1. Where carriers publish holiday ship-by dates

This skill never supplies carrier dates. Get them from the carrier each year and enter them with their URL.

| Carrier | Where | What you get |
| --- | --- | --- |
| USPS | A USPS Newsroom national release each fall (https://about.usps.com/newsroom/national-releases/) and the holiday shipping pages on usps.com (search usps.com for "holiday shipping deadlines"). | Last recommended mailing dates by mail class (USPS Ground Advantage, Priority Mail, Priority Mail Express) and by destination (contiguous US, Alaska, Hawaii, military APO/FPO/DPO). |
| UPS | The holiday shipping deadlines page on ups.com (search ups.com for "holiday shipping"); the address changes from year to year. | Last days to ship for air and 3 Day Select services, the holiday service schedule, and for UPS Ground a pointer to transit time by distance rather than one date. |
| FedEx | "Holiday Schedule & Last Days to Ship": https://www.fedex.com/en-us/holiday/last-days-to-ship.html | Last days to ship by service and the holiday operating schedule, including modified pickups (when checked, it noted modified Ground pickups on Dec 24 and Dec 31). |
| Regional carriers, 3PLs | Ask your account rep in writing. | Their cutoffs and closed days, which may be earlier than the national carriers'. |

For each date record the service, the "for delivery by" day, the destinations it covers, the URL and the day you checked it. Carriers can revise dates in December; re-check before the cutoff messaging goes live. If the carrier's date is "for delivery by Dec 25" and the carrier doesn't deliver on Dec 25, it also serves a Dec 24 target. For an earlier target, move it back by the same number of the carrier's delivery days.

## 2. Transit days by zone

- **Carrier maps and calculators.** UPS and FedEx publish ground transit-time maps by origin ZIP, and both have time-and-cost calculators: check one destination ZIP in each zone. USPS says Ground Advantage delivers in 2–5 days and may be slower to Alaska, Hawaii and offshore destinations (https://www.usps.com/ship/ground-advantage.htm).
- **Your shipping software.** ShipStation and Shopify Shipping show the estimated delivery for each service when you rate a shipment; rate a few addresses per zone.
- **Your own history (best).** Export last November–December shipments with ship date, delivery date, service and zone (ShipStation's shipments export, or your carriers' tracking reports). Count business days in transit by zone and service. Where most of a zone arrived in 3 days and a few in 4, enter `3-4`.
- **Peak allowance.** December transit often runs slower than normal estimates. Set `peak_extra_days` to the extra days you saw last December. With no history, 1 day for air and 2 for ground is a cautious starting point (the sample uses those); replace it with your own numbers. It is printed as your allowance, never as carrier data.

## 3. The zone mix

1. Export last November–December orders with the shipping ZIP (Shopify: Orders → Export) or shipments with the zone (ShipStation).
2. With ZIPs only: open the USPS Domestic Zone Chart (https://postcalc.usps.com/DomesticZoneChart), enter your warehouse ZIP, and map each order's first three ZIP digits to a zone. Use one carrier's chart for everything; the differences are small.
3. Count orders per zone into `destinations.csv`. Check Alaska, Hawaii, territories, PO boxes and APO/FPO separately; many services don't reach them on the same schedule.

## 4. Cutoff share and daily capacity

- **Cutoff.** Use the time your last carrier pickup or drop-off still gets same-day orders out, not the time the team hopes for. The checker uses one cutoff time. If a carrier picks up early on a given day (Dec 24 is common), write that day's copy with the earlier time, or add the day to `closed_dates` to be safe.
- **after_cutoff_pct.** In Shopify Analytics, a report of orders (or sales) by hour of day for 4–8 recent weeks, in the store's time zone; or export orders and count them by the hour they were created. Add up the share at or after the cutoff.
- **Capacity.** Last BFCM's best shipping day (ShipStation shipments by ship date), or orders packed per hour × staffed hours. Temporary staff or overtime goes in `peak_capacity` with its dates. A one-off weekend shift isn't modeled; approximate it by adding its output to the next working day's capacity in `peak_capacity`.

## 5. The FTC rule in plain terms (not legal advice)

The FTC's Mail, Internet, or Telephone Order Merchandise Rule (16 CFR Part 435) covers online orders. The FTC's business guide (https://www.ftc.gov/business-guidance/resources/business-guide-ftcs-mail-internet-or-telephone-order-merchandise-rule) says:
- You must have a reasonable basis for stating or implying that you can ship within a certain time. If you make no shipment statement, you must have a reasonable basis for believing you can ship within 30 days.
- If you then learn you can't ship on time, you must seek the customer's consent to the delay, or cancel the order and make a prompt refund.

What that means for holiday copy: give per-method dates with the cutoff time; change "ships next business day" before a known backlog; and if orders slip past what you stated, send a delay notice that offers to cancel. A "guaranteed by Christmas" line implies a shipping time and also raises the question of what you do when it's late. Rule text: https://www.ecfr.gov/current/title-16/chapter-I/subchapter-D/part-435. State laws may add requirements. Send any guarantee wording to whoever reviews legal copy.

## 6. Copy templates

Plain and dated. Name the method, give the cutoff time and time zone, and say "arrive by", not "guaranteed".

| Use | Template |
| --- | --- |
| Banner, cart, product pages | "Order by 1 pm ET Mon, Dec 14 for Standard shipping, or by 1 pm ET Mon, Dec 21 for Express, to arrive by Thu, Dec 24." |
| BFCM shipping note (FAQ, cart, order confirmation) | "Orders placed Nov 26 – Dec 2 ship within 3 business days." |
| Last-day reminder (email, SMS) | "Last day for Standard shipping to arrive by Thu, Dec 24: order by 1 pm ET today." |
| Expedited only | "Standard shipping will now arrive after Thu, Dec 24. For delivery by Thu, Dec 24, choose Express and order by 1 pm ET Mon, Dec 21." |
| E-gift cards | "Orders placed now will arrive after Thu, Dec 24. Gift cards are delivered by email right away." |
| Closure | "We're closed Fri, Dec 25. Orders placed after 1 pm ET Thu, Dec 24 ship Mon, Dec 28." |
| Fine print, if wanted | "Delivery dates are carrier estimates for addresses in the contiguous US." |

Avoid: "guaranteed" (unless you offer a remedy), "in time for Christmas" with no date, "ships today" during a backlog, and last year's dates left in macros and templates.

## 7. Making the changes

- **Shopify.** Announcement bar and banners: Online Store → Themes → Customize. Product page notes: the product template or a metafield block. Checkout rate names: Settings → Shipping and delivery → your shipping profile → rates. FAQ: Online Store → Pages. Gift cards are a product type delivered by email (https://help.shopify.com/en/manual/products/gift-card-products); feature them after the last cutoff.
- **ShipStation.** If Standard is rate-shopped between two services with different last safe dates, an automation rule can send every Standard order placed after the earlier cutoff with the later service, which keeps Standard open longer (the report gives the exact date and service). Remove the rule after the holiday.
- **Klaviyo (or your ESP).** Edit scheduled campaigns before their send date. Put the dated lines in flows (order confirmation, shipping confirmation, abandoned checkout) with a date-bounded block or by editing them on the switch dates. Schedule the last-day SMS for the morning of each cutoff.
- **Help desk.** Update the holiday shipping macros with the same dates (the Holiday Support Macros skill builds the full set).
- After each change, read it back: view the page logged out, place a test order to see the rate names, send a test email.

## 8. Re-run schedule

| When | Why |
| --- | --- |
| Now | Fix RISKY and unqualified promises; plan the calendar. |
| When carriers publish holiday dates | Add them with URLs; replace any sample or estimated rows. |
| The day the BFCM backlog clears | Put actual BFCM orders into demand.csv; confirm December ship dates. |
| A few days before cutoff messaging | Final check of every date and placement. |
| Each switch day | Confirm the switch went live (state: verified). |

## 9. Status update template

```
Holiday shipping check, <store>, as of <date>: <VERDICT>.
Last safe order dates (by <cutoff time and zone>): <rate/service> <date>, ...
BFCM: <backlog headline from the report>.
Fix now: <RISKY and WARNING items, one line each, with owner>.
Switches: <dates from the messaging calendar>.
Carrier dates: <source URLs, or "sample/estimated, not yet confirmed">.
Next re-run: <date>. Nothing has been changed yet.
```
