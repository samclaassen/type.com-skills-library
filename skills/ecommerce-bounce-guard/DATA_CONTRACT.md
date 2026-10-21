# Data contract: ecommerce-bounce-guard

## 1. Bounce bundle (`--bundle`, JSON, required)

`scripts/klaviyo_fetch.py` writes this file. You can also build it by hand from any ESP. UTF-8. Unknown keys are ignored.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `account` | string | yes | Store or account label, printed in the report title. |
| `as_of` | `YYYY-MM-DD` | yes | Last day covered. The report is deterministic for a given bundle. |
| `source` | string | no | ESP name for the title, e.g. `klaviyo`, `omnisend`. Default `klaviyo`. |
| `flows` | array | yes | `{id, name, status, trigger_type}`. `status` is the ESP's own value (`live`, `manual`, `draft`). |
| `flow_messages` | array | no | `{id, flow_id, name}`. Used to name the worst email in a flow. |
| `weekly` | series | yes | Bounce statistics per flow email, one bucket per week. |
| `daily` | series | yes | The same, one bucket per day. 21 days recommended. |
| `bounces` | array | no | Recent bounce events (the most recent N). Feeds the "who is bouncing" fingerprint. |

### Series (`weekly`, `daily`)

```json
{
  "date_times": ["2026-07-13T00:00:00+00:00", "..."],
  "rows": [
    {"flow_id": "RtA9cK", "flow_message_id": "AC1msg",
     "recipients": [21, 19, "..."], "bounced": [0, 1, "..."]}
  ]
}
```

- `date_times` holds the bucket start times. Every row's `recipients` and `bounced` lists must have exactly that many numbers.
- Each row is one email in one flow, and several rows can share a `flow_id`. The analyzer sums them per flow for the flow score and compares them per email to find the worst one. `flow_message_id` may be empty if your ESP only reports at flow level.
- Email only. Leave out SMS and push.
- Validation (any failure exits 2 with the reason): every `flow_id` must exist in `flows`, no negative numbers, and `bounced` ≤ `recipients` in every bucket.

**Weekly window:** at least 4 weeks, 12 recommended. The latest bucket is "current". If it has fewer than 25 recipients (for example, a partial week), the bucket before it is used. Every bucket except the last two forms the baseline.

### Bounce events (`bounces[]`)

| Field | Type | Notes |
| --- | --- | --- |
| `datetime` | ISO-8601 | When the bounce happened. |
| `email` | string | The recipient. **Personal data.** It is never printed in full; the report masks it (`ab***z@domain.com`). |
| `profile_created` | ISO-8601 or null | When the ESP profile was created. Drives the "< 48h old" signal. |
| `bounce_type` | `hard` / `soft` / null | |
| `flow_id` | string or null | Flow that sent the bounced email, or null for campaigns. |
| `campaign` | string or null | Campaign name when `flow_id` is null. |
| `active_on_site` | bool or null | Whether the profile has *any* Active on Site event. Use `null` for profiles that weren't checked. |

## 2. Shopify daily counts (`--shopify-daily`, CSV, optional)

Header row, then one row per day, covering the same window as `daily`.

| Column | Type | Notes |
| --- | --- | --- |
| `date` | `YYYY-MM-DD` | |
| `abandoned_checkouts` | integer | Abandoned checkouts created that day (Shopify Orders → Abandoned checkouts, or Admin API). |
| `orders` | integer | Orders placed that day. |

The analyzer compares the average per day before and after the first bad day. If checkouts rose 2× or more while orders rose less than 1.3×, that counts as a bot signal.

## 3. Building a bundle by hand

- **Klaviyo UI:** Analytics → custom report → Flow performance, grouped by flow message, with Recipients and Bounced. Export weekly for 12 weeks and daily for 21 days. For bounces, export the profiles in a segment of *Bounced Email at least once in the last 21 days*, with their created date.
- **Omnisend / Mailchimp / others:** export per-automation sent and bounced counts by week and by day in the same shape. Leave `active_on_site` null if the ESP doesn't track onsite activity. The report then skips that signal.
