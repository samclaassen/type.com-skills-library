# Client profiles

Agency skills in the Type library work across many clients. Every one of them
reads the same client profile format, so a profile set up once works with all
of them.

## Where profiles live

```
clients/
  <client-slug>/
    client.json        required — identity, accounts, goals
    brand-voice.md     optional — written by the brand-voice skill
```

`<client-slug>` is lowercase letters, numbers and single hyphens
(`harbor-and-vine`). A skill asked about "Harbor & Vine" resolves it by slug or
by the `name` field.

## client.json

Only `name` is required. Every other field is optional, and a skill that needs a
field it does not find says so by name and stops, rather than guessing.

```json
{
  "name": "Harbor & Vine",
  "website": "https://harborandvine.com",
  "industry": "dtc",
  "accounts": {
    "google_ads_customer_id": "123-456-7890",
    "google_ads_login_customer_id": "987-654-3210",
    "ga4_property_id": "properties/123456789",
    "search_console_site": "sc-domain:harborandvine.com",
    "meta_ad_account_id": "act_1234567890"
  },
  "competitors": ["https://competitor-a.com", "https://competitor-b.com"],
  "goals": ["Grow non-brand search revenue 20% by Q4"],
  "notes": "Brand never uses exclamation marks. Legal reviews all health claims."
}
```

## Rules every agency skill follows

1. **Never mix clients.** One run reads one client's profile and one client's
   data, unless the skill is explicitly a portfolio view across clients, and
   then it labels every row with its client.
2. **Client-facing output is marked as a draft** for the account lead to review.
   Nothing is sent to a client automatically.
3. **Writes to a client's accounts need explicit approval** in the conversation,
   naming the client and the change, every time.
4. **Missing profile or field:** say exactly what is missing and how to add it.
