# Data contract — b2b-newsletter-writer

All files are UTF-8. JSON must be a single object. Dates are `YYYY-MM-DD`. The
scripts never read the wall clock; the issue date comes from `draft.json`.
Any schema violation exits `2` with `<file>: <field>: <problem>`, for example
`draft.json: sections[2].body: missing`. Missing values are reported, never guessed.

## 1. `draft.json` — the newsletter the agent wrote (required for lint/render)

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `issue_date` | string `YYYY-MM-DD` | yes | Drives "Week of Oct 2, 2026" in the header. |
| `subject_lines` | string[] | yes, >= 1 | Without surrounding quotes. Lint requires exactly `subject_line_count`. |
| `preheader` | string | no | Inbox preview text; rendered as a hidden span. |
| `opener` | string | yes | 2-3 sentences. Blank-line separated paragraphs allowed. |
| `sections` | object[] | yes, >= 1 | In reading order. |
| `sections[].headline` | string | yes | Plain text. |
| `sections[].body` | string | yes | Commentary. Paragraphs separated by a blank line (`\n\n`). Inline `**bold**` and `[text](https://…)` only. |
| `sections[].source_url` | string | conditional | Absolute http(s) URL. Required when the body cites a source, a figure or a quote. `""` for pure commentary or own-product notes without figures. |
| `sections[].source_title` | string | no | Link text. Falls back to the domain. |
| `cta.type` | string | yes | Must equal config `cta_type`. |
| `cta.text` | string | yes | The CTA paragraph. For `reply`, must contain a question. |
| `cta.url` | string | product/content/event | Absolute http(s) URL. Omit for `reply`. |
| `cta.button_label` | string | no | Defaults per type from `rules.json` → `render.default_button_labels`. |
| `cta.deadline` | string `YYYY-MM-DD` | no | Only when the brief states a real deadline. Turns urgency words from FAIL into WARN. |
| `sign_off` | string | no | From the voice profile, e.g. `— Maya`. |
| `facts` | object[] | no (but needed for any figure) | Evidence for figures and quotes. |
| `facts[].quote` | string | yes | Verbatim sentence copied from the fetched source (or from the brief). |
| `facts[].source` | string | yes | The source URL it was fetched from, or the literal `"brief"`. |
| `brief_topic_count` | integer | no (always set it) | Number of topics in the brief. Set it on every draft. More sections than this FAILs as padding; when it is below config `sections`, a matching smaller section count passes. If omitted on a reduced draft, lint WARNs and names the line to add. |

Keys starting with `_` (e.g. `_note`) are ignored.

### What counts as a figure, a quote, a word

- **Figure:** any number token in visible copy: `41`, `23%`, `$40`, `1,200`, `3.5x`, `10:30`, `40k`.
  Numbers inside URLs and digits glued to letters (`B2B`, `Q3`, `GA4`) are ignored. Four-digit
  years 1900-2099 are ignored when `ignore_years` is true. A figure is verified when its numeric
  value (commas, `$`, `%`, `x`, `k` stripped) appears in any `facts[].quote` or in the brief.
- **Quote:** text inside straight or curly double quotes with at least `quote_min_words` words.
  Verified when it appears verbatim (case, apostrophe style and outer punctuation normalised) in
  a fact quote or the brief.
- **`[unverified]` marker:** a figure or quote in the same sentence as the marker is a WARN, not a FAIL.
- **Word:** a whitespace-separated token containing a letter or digit, after link targets are removed.
  Body total = opener + every headline and section body + `cta.text` + `sign_off`.
  Subject lines, preheader, button label and source titles are excluded.

## 2. Newsletter config JSON (required for lint; optional for render)

The spec's config keys plus script additions. Template: `newsletter.json`.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `sections` | int 1-10 | yes | Target section count. |
| `max_word_count` | int >= 50 | yes | Body word limit. |
| `subject_line_count` | int 1-10 | yes | |
| `cta_type` | `product` \| `content` \| `event` \| `reply` | yes | Blank exits 2 (set it in Step 0). |
| `format` | `html` \| `markdown` | no (default `html`) | Default render format. |
| `from_name`, `audience`, `newsletter_name` | string | no | Shown in the masthead / review header. Blank `from_name` is a WARN. |
| `voice_file`, `past_issues` | string | no | Used by the agent, not the scripts. |
| `allow_emoji` | bool | no (false) | Emoji in subject lines. |
| `extra_banned_phrases` | string[] | no | Added to the banned list. A trailing `*` means prefix match (`empower*`). |
| `extra_acronyms` | string[] | no | Caps tokens allowed in subject lines. |
| `brand` | object of `#RRGGBB` strings | no | Overrides `rules.json` → `render.brand` (`accent_color`, `button_text_color`, `text_color`, `muted_color`, `background_color`, `content_background_color`, `rule_color`) and `font_family`. |

## 3. Brief (optional for lint, recommended)

Plain text or markdown, exactly as the user gave it (plus your notes). Every figure and quote in it
counts as verified, so do not paste unverified claims from the web into the brief.

## 4. Past issues (voice-stats input)

One markdown file per issue. Optional first line `Subject: <subject line>`. `#`-headings are
treated as section headlines, a line starting with `**Label:**` as a recurring label, and the last
non-empty line as the sign-off. When more than `voice_issue_count` (3) files are passed, the newest
3 by filename are used, so name files `YYYY-MM-DD-<slug>.md`.

Typical source fields when exporting:

| Source | Subject | Body |
| --- | --- | --- |
| Google Docs (Drive `files.export`, `text/plain`) | Doc title or first line | export text |
| Beehiiv `GET /v2/publications/{id}/posts` | `subject_line` (or `title`) | `content.free.web` (HTML → strip to text) |
| Mailchimp `GET /3.0/campaigns/{id}/content` | `settings.subject_line` from the campaign | `plain_text` |
| Kit `GET /v4/broadcasts/{id}` | `subject` | `content` (HTML → strip to text) |
| HubSpot marketing emails `GET /marketing/v3/emails/{id}` | `subject` | `content` widgets (strip to text) |
| Public archive page (web fetch) | page `<title>` / h1 | article text |

## 5. `rules.json`

All thresholds and word lists: section word range, opener sentence range, subject/preheader/headline
character limits, acronym list, banned phrases and openers, spam-trigger and false-urgency words,
clickbait patterns, attribution cues, stance markers, placeholder regexes, render brand defaults
and the `html` rules for `check-html` (forbidden tags, forbidden CSS patterns with severity, max
width, byte limits, allowed link schemes, merge-tag patterns).

## Outputs

- `lint`: markdown report, or `--json` with `result`, `fail`, `warn`, `word_budget`, `cut`, `findings[]`, `passes[]`. Exit 0 (no FAIL) / 1 (FAIL) / 2 (bad input).
- `render`: `html` (full XHTML document), `markdown` (review format), or `text` (plain-text alternative, wrapped at 72). `--out` writes a file.
- `check-html`: markdown report or `--json`. Exit 0 / 1 / 2.
- `voice-stats`: markdown table or `--json` of measurements, labelled as measurements, not a profile.
