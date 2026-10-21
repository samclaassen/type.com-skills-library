# BFCM Tracking & Attribution Preflight: Playbook

Every change in this playbook needs the user's explicit approval. After a change, place a real test order (or use each platform's test-event tool), read the setting back, and re-run the preflight once 3–7 days of new data exist. Menu labels move; if a path below doesn't match, look for the named page or report. Public documentation was reviewed on 2026-10-07; no menu path, account setting or end-to-end ad link has been tested in a live merchant account.

## 1. Meta: deduplication, Conversions API and Event Match Quality

**Check dedup.** Events Manager → Data sources → your dataset (pixel) → Overview → click **Purchase**. Look at:
- **Connection method**: Browser and Server should both be present, with coverage assessed against consented, eligible storefront orders for the same reporting period.
- **Event deduplication**: the share of server events that matched a browser event. Healthy setups merge most of them. If it is near zero while both sources fire, investigate duplicate delivery using event-level IDs; aggregate counts alone do not prove every purchase was counted twice.
- **Diagnostics** tab: Meta warns about missing or mismatched `event_id`.

**Why dedup breaks.** Meta merges a browser and a server event only when both have the same `event_name` and the same `event_id` (within 48 hours). It breaks when:
- a second server-side source was added (a CAPI gateway, server-side GTM, a tracking or "attribution" app) next to Shopify's Facebook & Instagram app, and it sends its own event IDs;
- a custom pixel or GTM tag sends `Purchase` with a different event name or no `event_id`;
- the app or pixel was reinstalled or reconnected to a different dataset.

**Fix.** Keep one server source per dataset. If you use Shopify's Facebook & Instagram app, let it send both browser and server events. If you must use another CAPI source, make it reuse the browser event's `event_id` (often the Shopify order ID or checkout token) and remove the duplicate. Confirm matching event IDs and deduplication using test events, then compare like-for-like counts after reporting latency. The 0.85–1.10x band is only a configurable screening heuristic, not proof of correct tracking.

**Conversions API review.** Shopify admin → Sales channels → Facebook & Instagram → Settings → Share data settings. Record the current setting and supported server-side integration. The merchant must choose a sharing level consistent with their consent, privacy disclosures and applicable requirements; do not automatically select Maximum or treat a deliberate privacy restriction as a defect. See [Shopify Meta pixel settings](https://help.shopify.com/en/manual/promoting-marketing/analyze-marketing/meta-pixel).

**Event Match Quality (EMQ).** Overview → Purchase → Event Match Quality. Aim for "Good" or better on Purchase. EMQ rises with hashed email and phone, external ID, `fbp`/`fbc` cookies, IP and user agent on server events. Only send fields allowed by the merchant's consent and privacy configuration. Low EMQ means Meta matches fewer purchases to people, not that counts are wrong.

**Test.** Events Manager → Test events → open the store with the test code, buy something (a 100%-discount product works), and confirm one Purchase from Browser and one from Server, marked *Deduplicated*.

## 2. Shopify: customer events and web pixels

- **Settings → Customer events** lists every **app pixel** (installed by apps) and **custom pixel** (code you added). Look for two pixels sending the same platform's Purchase, or a custom pixel left over from an agency. Each extra one is a double-count risk.
- Legacy *Additional scripts* on the Thank you / Order status pages and `checkout.liquid` stop running once a store's checkout and thank-you pages are upgraded to checkout extensibility. A purchase tag that lived there stops firing without any error. Move it to a custom pixel or the platform's app.
- **Customer privacy** settings and your consent banner decide when pixels may fire. In regions where consent is required, purchases from shoppers who declined won't reach the browser pixel. That is expected; don't "fix" it by bypassing consent.
- A theme publish or app install is the most common reason a browser pixel drops to zero for a day or two. Test an order after every one.

## 3. Google Ads: purchase conversion, value and enhanced conversions

- Goals → Conversions → Summary. Exactly one **primary** purchase action should count purchases (others secondary), with *Count: Every*. Two primary purchase actions double-count.
- **Value**: open the action's recent conversions and compare value per conversion with Shopify net sales per order. A 1.1–1.4x difference is an investigation flag, not a diagnosis. Reconcile matched orders, currencies, tax, shipping, refunds, attribution windows and reporting dates before proposing a value-definition change. Agree on a consistent revenue basis; do not divide ROAS by an aggregate discrepancy ratio. Don't change it in the middle of BFCM: Smart Bidding will react to the jump.
- **Enhanced conversions**: Goals → Settings → Enhanced conversions, on and set up for your tag or the Google & YouTube app. Then the action's Diagnostics should show enhanced conversions as active.
- Keep **auto-tagging** on (Admin → Account settings). A gclid without UTMs is normal and GA4 reads it; add the final URL suffix in §4 so Shopify reports see Google too.
- Diagnostics: Goals → Conversions → the action → Diagnostics shows tag health and recent drops.

## 4. UTM naming standard

Rules: lowercase, no spaces (use `_`), one `utm_source` per platform, `utm_medium` from a short fixed list, and each platform's own macro syntax. Put placement or ad-set detail in `utm_content`/`utm_term`, never in `utm_source`.

| Platform | Where | Template |
| --- | --- | --- |
| Meta | Ad URL parameters (candidate template; confirm available macros and ad-format support in the account) | `utm_source=facebook&utm_medium=paid_social&utm_campaign={{campaign.name}}&utm_content={{ad.name}}&utm_term={{adset.name}}` |
| Google Ads | Account (or campaign) settings → Final URL suffix | `utm_source=google&utm_medium=cpc&utm_campaign={campaignid}&utm_content={creative}&utm_term={keyword}` |
| TikTok | Ad level → Destination → URL parameters | `utm_source=tiktok&utm_medium=paid_social&utm_campaign=__CAMPAIGN_NAME__&utm_content=__CID_NAME__&utm_term=__AID_NAME__` |
| Klaviyo | Settings → Other → UTM tracking (account defaults; verify each campaign/flow override) | `utm_source=klaviyo&utm_medium=email` (`sms` for SMS), campaign = campaign or flow name |
| Pinterest, others | Ad-level URL parameters | `utm_source=pinterest&utm_medium=paid_social&utm_campaign=<name>` |

Approved `utm_medium` values: `paid_social`, `cpc`, `email`, `sms`, `affiliate`, `influencer`, `organic_social`.

Macro syntax differs: Meta `{{campaign.name}}`, Google ValueTrack `{campaignid}`, TikTok `__CAMPAIGN_NAME__`. A Meta template pasted into TikTok shows up as a literal `{{campaign.name}}` in Shopify. After any approved change, use the platform's URL testing/preview tools and inspect the resolved landing URL; preview macro substitution can differ from live delivery. Never assume a saved template has resolved correctly.

Cleaning old spellings: you can't rewrite past orders. Standardise new ads now, and in reports map the old spellings (`fb`, `Facebook`, `ig`, `instagram` → `facebook`) so BFCM compares like with like.

## 5. TikTok

Events Manager → your pixel → Overview / Diagnostics. Review the TikTok app's pixel and Events API setup within the merchant-approved consent and data-sharing configuration. Do not increase sharing automatically. Confirm the purchase event and shared `event_id` deduplication using the integration's current test tools. Check URL parameter support for each ad format rather than assuming one setting covers every ad.

## 6. Reading last year's claim gap and budgeting off MER

- Platforms apply different attribution windows, models and view/click rules. Several platforms can credit the same order, so their summed attributed revenue is not deduplicated store revenue. Reporting dates, consent, refunds and value definitions can also create differences.
- **Attribution gap ratio** = platform-attributed revenue ÷ Shopify revenue assigned to that platform's UTMs. This is a descriptive comparison of unlike attribution methods. It does not measure overclaim, incrementality or true channel contribution; a zero denominator makes the ratio undefined.
- Never use this ratio as a planning discount or divide platform ROAS by it. Review tracking evidence separately and use experiments or a suitably validated measurement model for incremental channel contribution.
- **Blended MER** = Shopify net sales ÷ total ad spend on the same currency, date and revenue basis. Use it with contribution margin, cash constraints, seasonality and reporting latency for merchant-approved budget guardrails. It includes non-ad-driven sales and is not causal ad ROI. Platform ROAS may inform within-platform comparisons after consistent attribution settings; it cannot be added across platforms.
- Recheck Shopify net sales, total spend and MER during BFCM, noting incomplete data before recommending action.

## 7. Timeline to BFCM 2026 (Black Friday Nov 27, Cyber Monday Nov 30)

| By | Step |
| --- | --- |
| Now – Oct 31 | Run the preflight. Fix critical items first: dedup, CAPI, pixels at zero. |
| Nov 10 | All tracking and UTM fixes in place. |
| Nov 13 | Re-run the preflight. If READY, Nov 13–26 is two clean weeks of data for planning and for the platforms' algorithms. |
| Nov 17 | BFCM budgets and scaling rules set from blended MER. |
| Nov 20 | Change freeze until Dec 1: no theme publishes, new apps, pixel, consent or checkout changes. |
| Nov 24 | Final preflight. |
| Nov 26 – Dec 1 | Every morning: each platform's purchases vs Shopify orders for yesterday, and blended MER. |
| Early Dec | Build this year's last-BFCM file while the numbers are fresh. |

## 8. Status update template

```
**BFCM tracking preflight: <store>**, <date>. Verdict: <READY | FIX BEFORE BFCM | NOT READY>

Detected
- <Possible Meta duplicate delivery since <date>: X events vs Y eligible orders; event-ID verification pending>
- <N of M orders (P%) without UTMs; top page <path>>
- Last BFCM: platforms claimed $A vs $B in Shopify (C x). MER was D.

Recommended (nothing changed yet), fix by <Nov 10>
1. <fix>
2. <fix>

Applied: <item, date, who>    Verified: <item, preflight run date>
```

## 9. Source checks and remaining verification

Public documentation reviewed 2026-10-07; these checks validate documentation, not a live merchant implementation.

- [Google ValueTrack](https://support.google.com/google-ads/answer/6305348) documents `{campaignid}`, `{creative}` and `{keyword}`. The suffix example is for compatible campaign types; keywordless traffic can return an empty keyword. Performance Max and Demand Gen have different supported parameters. Use a final URL suffix without `?`; a tracking-template field instead needs the landing URL placeholder.
- [TikTok UTM macros](https://ads.tiktok.com/resources/help/article/track-offsite-web-events-with-utm-parameters) documents `__CAMPAIGN_NAME__`, `__CID_NAME__` (creative name) and `__AID_NAME__` (ad-group name). Upgraded Smart+ adds different ad-bundle macros; use its current URL builder for that format.
- [Klaviyo UTM settings](https://help.klaviyo.com/hc/en-us/articles/115005247808) documents global defaults and message overrides. The values above are this collection's suggested naming convention, not guaranteed account defaults.
- Meta's dynamic template is a candidate pending verification against the account's current URL builder and official help; it was not independently verified from accessible Meta documentation in this review. Do not batch-apply it until a resolved URL has been checked.
