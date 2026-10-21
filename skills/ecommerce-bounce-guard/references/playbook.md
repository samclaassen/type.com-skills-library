# Ecommerce Bounce Guard: Playbook

Every change in this playbook needs the user's explicit approval. After making a change, read the setting back, then re-run the audit 48–72 hours later to confirm it worked.

## 1. Klaviyo UI paths (no-API fallback)

- **Bounce rate per flow email:** Flows → open the flow → Analytics (or Analytics → custom report "Flow performance"). Add the **Bounce rate** and **Bounced** columns, set the range to the last 12 weeks, and group by week, then by day.
- **Bounced profiles:** Analytics → Metrics → `Bounced Email` → Activity feed, filtered by date. Or build a segment: *What someone has done → Bounced Email at least once in the last 30 days*.
- **Account deliverability:** Analytics → Deliverability. Check the bounce, spam and unsubscribe benchmarks and any account-health warnings.

## 2. Flow filters (contain bot enrollments, keep real customers)

Apply to **Abandoned Checkout/Cart**, **Browse Abandonment** and **Welcome/Signup** flows. Edit them in the UI: Flow → Trigger → Flow filters / Trigger filters. Only turn on "apply to profiles already in flow" after spot-checking, because it removes profiles from sends already in progress.

Profile (flow) filters, all required:
1. `Active on Site` **at least once** in the last 30 days. When the store has Klaviyo onsite tracking, this is the best single discriminator: scripted checkouts don't run the onsite JavaScript. Confirm tracking is installed first. Without it, this filter blocks real customers too.
2. `Bounced Email` **zero times over all time**.
3. `Marked Email as Spam` zero times over all time.
4. Optional: `Email` **doesn't end with** each bad domain found in the report, and **doesn't contain** obvious patterns.

Trigger filters on `Checkout Started`:
- `$value` > 0
- `Item Count` ≥ 1
- Optional, if the attack targets one item: `Items` doesn't contain the targeted SKU or product.

Welcome flow fed by a form: turn on **double opt-in** for that list while the attack lasts, and enable the form's spam protection.

Before applying, estimate how many real recipients the filters would exclude by previewing the equivalent segment. Report it as "filters will exclude ~N/week, of which ~M look real".

## 3. Pause options (when filters can't go in right away)

| Action | Effect | Use when |
|---|---|---|
| Set the bouncing email to **Manual** | Sends queue instead of going out. Later emails still send if reached | Fastest way to stop the bleeding email |
| Set the flow to **Manual** / **Draft** (UI, or `PATCH /api/flows/{id}` with `{"data":{"type":"flow","id":"…","attributes":{"status":"manual"}}}`, which needs a key with `flows:write`) | The whole flow stops sending | Several emails are bouncing |
| Leave live | Reputation damage continues | Never, above 5% bounces |

Tell the user that queued Manual messages pile up and must be reviewed and cleared before going live again. Otherwise the whole batch of bounces fires at once.

## 4. Suppression

- Klaviyo **auto-suppresses** hard-bounced addresses, so addresses that already bounced are covered.
- What's left are bot profiles that **haven't been mailed yet** (queued in the flow, or created recently). Build a segment: *created in the last N days* AND *Checkout Started* AND *never Active on Site* AND *never Placed Order*, optionally adding the report's bad domains. Review 20 samples. Then bulk-suppress with `POST /api/profile-suppression-bulk-create-jobs` and `{"data":{"type":"profile-suppression-bulk-create-job","attributes":{"segment_id":"…"}}}` (or `list_id`, or an explicit `profiles` list). This needs `profiles:write`. Poll the job until `status=complete`, then report `completed_count` and `skipped_count`.
- Never suppress based on what an address looks like alone. Real people have odd addresses.

## 5. Shopify hardening (upstream)

1. **Bot protection:** in Shopify settings, confirm bot protection (hCaptcha) is on for checkout, account login and creation, and contact/newsletter forms.
2. **Inspect abandoned checkouts:** Orders → Abandoned checkouts, filtered from the onset date. Look for bursts, a single SKU, missing or nonsense shipping addresses, repeated locales, and checkouts with no customer account. The Admin GraphQL `abandonedCheckouts` query returns the same data for larger volumes.
3. **Edge rules:** if the store is behind Cloudflare or another WAF, rate-limit `/cart/add`, `/cart.js` and `/checkouts/*` per IP or ASN, and challenge datacenter ASNs.
4. **Bot-blocking apps:** consider a fraud or bot-filtering app if the attack continues after hCaptcha is on.
5. **Email validation at capture:** use real-time verification (Kickbox, ZeroBounce, NeverBounce) on forms and, where possible, at checkout email entry. Or use a Klaviyo webhook step that validates the address and suppresses invalid ones before email 1, with a 15–30 minute time delay before email 1 so validation can finish.
6. **Report to Shopify support** with the onset date and patterns. The same signature appearing across Klaviyo and Omnisend accounts points to a platform-side source.

## 6. Recovery plan (rebuild reputation before BFCM)

Days 0–2 (stabilize)
- Bounces on new sends under 1%, confirmed by re-running the audit.
- Authentication: SPF and DKIM pass and align with the From domain, DMARC is at least `p=none` with reporting, and the one-click unsubscribe header is present (Gmail and Yahoo bulk-sender rules). Use a dedicated sending domain if possible.
- Set up and check **Google Postmaster Tools** (domain reputation, spam rate, authentication) and **Microsoft SNDS**.

Weeks 1–2 (engaged-only ramp)
- Send campaigns only to engaged segments: opened or clicked in the last 30 days (or, adjusting for Apple Mail Privacy Protection, clicked in 60 days or purchased in 90).
- Increase volume by no more than 20–30% per send day. Don't send to the full list.
- Targets: spam complaints under 0.1% (hard ceiling 0.3%), bounces under 1%, and Postmaster reputation steady or rising.

Week 3 onward (widen)
- Widen to people engaged in the last 60 days, then 90, only while the metrics hold. Sunset or re-permission anyone with no engagement in 180 days.
- Plan BFCM sends for engaged audiences first. If reputation is still "Medium" or "Low", hold larger audiences for later in the window.

If Klaviyo has flagged or paused the account, follow its deliverability team's steps and timeline first. They override this ramp.

## 7. Other ESPs

The same workflow applies; only the tools change.
- **Omnisend:** Reports → Automation → bounces per workflow. Add workflow filters (for example, the contact has visited the site, or the `Started checkout` event has a value above 0), and turn on double opt-in for forms.
- **Mailchimp (Shopify):** Customer Journeys → report → bounces. Add a starting-point filter and a conditional branch.
- **Attentive / Postscript (SMS):** bot checkouts can trigger SMS too. Check for spikes in carrier errors or undelivered messages.
- **Any ESP:** build the bundle by hand (`DATA_CONTRACT.md` §3) and run the analyzer. Then filter, validate at capture, and ramp.

## 8. Report template

```
**Deliverability alert: <store> (<ESP>)**
Status: DETECTED | CONTAINED (applied, verifying) | RESOLVED (verified)

What's happening
- <Flow> email 1: X of Y bounced (Z%) last week. Baseline over the prior 10 weeks: A–B%.
- First bad day: <date> (<n>/<m>). Clean days before it: <date range> (<n>/<m>).
- Also affected: <flows> (same onset, so one upstream source).

Why we think it's bots
- N% of bounced profiles were created < 48h before bouncing; N% never visited the site; N% have machine-generated addresses.
- Shopify: abandoned checkouts up Nx since <date> with no matching rise in orders.

Separate from spam placement: <seed-test result, if any>.

Recommended (nothing changed yet)
1. Add flow filters: <list>. Estimated real customers excluded: ~M/week.
2. Set <email> to Manual until the filters are live.
3. Suppress <N> queued bot profiles (segment <name>).
4. Shopify: confirm hCaptcha; add validation at capture.
5. Engaged-only ramp until BFCM: <dates>.

Revenue tradeoff: <flow> drove $X in the last 30 days; the filters keep ~Y% of real sends.
```

## 9. Support ticket drafts (drafts only; never send without approval)

**Klaviyo:**
> Since <date>, the first email in our <flow> flow has hard-bounced at <Z%> (<X>/<Y>), up from a <A%> baseline. The bounced profiles came from Shopify `Checkout Started` events that appear to be bot-generated (<signals>). We've added flow filters and suppressed queued profiles. Could you (1) confirm whether our account's deliverability status was affected, (2) advise on any remediation needed before BFCM, and (3) confirm whether you're seeing this pattern across Shopify accounts since late September 2026?

**Shopify:**
> Starting <date>, we're seeing a burst of abandoned checkouts with invalid emails (<count>, <patterns>, <SKU if any>) and no matching orders. They trigger our email flows and damage our sender reputation. Bot protection is <on/off>. Can you confirm whether this is a known bot campaign, and what additional checkout protection is available?
