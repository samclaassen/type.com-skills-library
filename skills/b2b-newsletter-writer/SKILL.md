---
name: b2b-newsletter-writer
description: Turns a topic brief, links, or a content dump into a review-ready B2B newsletter in your brand voice (subject lines, preheader, opener, sourced commentary sections, CTA), lints it for length, AI-slop phrasing, spammy subject lines and unsourced numbers, and renders email-safe HTML. Use when someone says "write this week's newsletter", "newsletter from these links", "draft our email update", or on a weekly schedule fed by a content queue. Drafts only; never sends.
---

# B2B Newsletter Writer

Turns a two-minute brief into a newsletter people read: sections that say what you think, figures that come from somewhere, subject lines that don't look like spam. Bundled tooling checks the draft before anyone sees it.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the files listed with this skill (scripts, config, sample data, `run.sh`, `expected_output.txt`; see **Minimum files** below for what is strictly required) are in one working folder alongside this SKILL.md. All files sit flat in the skill's root; there are no subfolders. Some environments load only SKILL.md. If yours did, fetch each file from this skill's published files and write it into that folder under the same filename. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `b2b-newsletter-writer`.
2. **Check the copies are exact.** Compare each file's size in bytes (not characters) with the published version, and its hash where your tools report one. A copy written out from the published file is fine once its byte size and hash match. Never run a script you summarised or rebuilt from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 …` and `bash run.sh`.
4. **Try the sample first.** Run `bash run.sh`. Its output should match `expected_output.txt` exactly. If it doesn't, stop and report the first line that differs rather than running on real data.

**Minimum files.** To run on real data you need only `newsletter.py`, `rules.json` and `newsletter.json` (the blank config template). Everything else is reference or sample/test material: `sample_*.json`, `sample_*.md`, `past_issue_*.md`, `expected_output.txt`, `run.sh` and `tests.sh` are only for the sample check and tests, and `DATA_CONTRACT.md`, `writing-guide.md` and `voice-profile-template.md` are docs you can read in place. The sample check (step 4) is still recommended, but it is optional when copying the extra files is costly; in that case, verify the byte size and hash of the three required files (step 2) before running.

## Config

```yaml
voice_file: ""            # brand voice/tone doc, or blank to detect from past issues
from_name: ""             # sender name, e.g. "Maya Okafor, Harborline"
audience: ""              # one line, e.g. "Ops leads at 10-80 person agencies"
sections: 3               # target number of content sections
max_word_count: 600       # whole body: opener + sections + CTA + sign-off
cta_type: ""              # product | content | event | reply. Set in Step 0
subject_line_count: 3     # subject line options to generate
format: html              # html | markdown. html for email tools, markdown for review
past_issues: ""           # Drive/Docs folder, Space folder, or ESP archive URL
# Additions used by the scripts:
newsletter_name: ""       # masthead text
allow_emoji: false        # emoji in subject lines
extra_banned_phrases: []  # voice-specific phrases to ban (from the voice profile)
extra_acronyms: []        # caps words that are legitimate (product names, etc.)
brand: {}                 # accent_color etc. as #RRGGBB; defaults in rules.json
```

The scripts read these values as JSON. `newsletter.json` is the blank template and `sample_config.json` is a filled example. Lint thresholds and word lists live in `rules.json`, not in code.

## Step 0 — First run: confirm sources and settings

Run this step while `cta_type`, `from_name` or the voice source is blank (in `b2b-newsletter-writer-settings.md`, if it exists), or when a configured source stops responding.

1. **Detect.** Check what this Space can actually read. Make one real read call per source: list one file in the `past_issues` folder (Google Drive), or fetch one campaign from a connected ESP (Mailchimp, Beehiiv, Kit, HubSpot, Customer.io), or fetch the archive URL. A tool that is listed but fails its read call does not count as connected. In Type, also check workspace connections that exist but are not added to this Space (for example a workspace Google Drive or Beehiiv connection): name them and offer to add them to the Space. Never silently treat them as absent. Also note whether a send-capable integration is connected. It will not be used to send, but say so.
2. **Propose.** For example: "Beehiiv is connected and has 14 sent posts. I'll calibrate voice from the last 3, sign as 'Maya Okafor, Harborline', and point the CTA at a product page (`cta_type: product`). OK?" If more than one past-issue source exists, ask which one. If none exists, offer the clean professional default.
3. **Wait for confirmation.** Never pick a source or a `cta_type` silently. The script exits 2 on a blank `cta_type` for this reason.
4. **Save** the confirmed values to a small settings file in the user's working folder or Space, named `b2b-newsletter-writer-settings.md` (it is not part of the skill), and write the same values into the run's `config.json` (copied from `newsletter.json`). Read that settings file at the start of later runs. Never edit this SKILL.md or other skill files to store settings. If you can't write files, give the user the exact lines to save.

If an automation started the run and nobody is around to confirm, post one setup question naming the missing values, then exit without drafting.

## Triggers

- **Manual:** "Write this week's newsletter about [topics]", "Newsletter from these links: [URLs]".
- **Schedule:** weekly, pulling from a curated content queue (a doc, a Notion/Airtable view, a Slack channel of saved links) or the blog's newest posts.

## Step 1 — Parse the brief and fetch sources

Pull out the topics, links, key points, any explicit angle, and the CTA target. Count the topics. This is `brief_topic_count`.

For **every URL**, fetch the page with the web fetch tool. Use the browser only for pages that need sign-in. From each page, record the title, the canonical URL, and 1–3 **verbatim** sentences that contain any figure, claim or quote you might use. These go in `facts[]` as `{quote, source}`. Figures from the brief itself use `source: "brief"`. If a fetch fails or hits a paywall, say so in the review note. Write that section from the brief alone, or drop it. Never summarise a page you haven't read, from its title or from memory.

## Step 2 — Voice calibration

Priority: `voice_file` → the last 3 `past_issues` → the default voice.

**Getting past issues** (save each as markdown, with `Subject: …` as the first line when known):
- **Google Drive/Docs:** `files.list` with `q="'<folderId>' in parents and trashed=false"`, `orderBy=modifiedTime desc`, `pageSize=3`. Then `files.export` each Doc with `mimeType=text/plain`.
- **Space or local folder:** the 3 newest files by date in the filename.
- **Beehiiv:** `GET /v2/publications/{id}/posts?status=confirmed&order_by=publish_date&direction=desc&limit=3&expand[]=free_web_content`.
- **Mailchimp:** `GET /3.0/campaigns?status=sent&sort_field=send_time&sort_dir=DESC&count=3`, then `GET /3.0/campaigns/{id}/content` and use `plain_text`.
- **Kit:** `GET /v4/broadcasts`, take the 3 newest sent ones, then `GET /v4/broadcasts/{id}` for `content`.
- **Public archive URL:** web-fetch the archive page, then the 3 newest issue links.

Run `python3 newsletter.py voice-stats --issues <files>`. It measures sentence length, person, contractions, punctuation, headings, opener length, recurring labels and the sign-off. Turn the numbers into a **voice profile** with `voice-profile-template.md` (example: `sample_voice_profile.md`), covering: sentence length, person, formality, recurring structures, how the opener works, the CTA pattern, the sign-off, and banned phrases. Show the profile in the review post on the first run. If it surfaces voice-specific phrases to avoid, add them to `extra_banned_phrases`.

**No voice source:** use the default. Plain, direct, second person, contractions allowed, an average of 12–16 words per sentence, no exclamation marks. Say in the post that voice calibration will improve once past issues are available.

## Step 3 — Write the sections

Map topics to sections. Use `min(sections, brief_topic_count)` and **never pad with filler** to reach the configured count. Always set `"brief_topic_count": <number of topics in the brief>` in `draft.json`, every run, even when it equals `sections`. Lint uses it to FAIL padding (more sections than brief topics) and to accept a reduced section count without a WARN. Each section has:

- **A descriptive headline** that states the point, not a teaser. Write "Scope creep is still the biggest margin leak", not "You won't believe this stat". Use sentence case and keep it to 70 characters or fewer.
- **80–150 words of commentary that states a take.** Lead with what happened, attributed ("The Studio Ops Collective surveyed 310 agencies…"). Then say what you think and why it matters to *this* audience. End with something the reader can do, using the voice profile's recurring structure if it has one (for example "**Steal this:** …"). A summary restates the source. A take disagrees, ranks, predicts or recommends.
- **The source link**, with `source_url` and `source_title`, whenever the section draws on a source.

Use quotation marks only for real quotes copied verbatim from a fact. Never use them for emphasis or labels. Don't copy 8 or more consecutive words from a source without quote marks. Commentary means a point of view, not a rewrite.

## Step 4 — Opener, CTA, subject lines, preheader

- **Opener:** 2–3 sentences that frame the issue's theme as a claim the sections then prove. No greeting, and no "Welcome to this week's edition", "Happy Friday" or "In this issue". The first sentence should be one a reader might forward.
- **CTA, matched to `cta_type`:** `product` names a specific feature or trial and what it does for the reader. `content` names the resource and the one thing they will get from it. `event` gives the name, date and registration link. `reply` asks one genuine question the sender wants answered, with no link. Button labels name the action ("Open Capacity Forecast"), never "Click here" or "Learn more".
- **Subject lines (`subject_line_count` options):** under 50 characters. Lean on curiosity or on specificity, not both in one line. No ALL CAPS words except real acronyms, no emoji unless `allow_emoji`, no spam-trigger words (free, guarantee, act now…), no fake "RE:"/"FW:", and no urgency unless the brief has a real deadline (`cta.deadline`). Never promise what the issue doesn't deliver.
- **Preheader:** 100 characters or fewer, adds information the subject line doesn't, and doesn't repeat it.

## Writing rules (all copy)

**Banned phrases** are filler that marks text as machine-written. The full list is in `rules.json` and includes: *in today's fast-paced world, ever-evolving landscape, game-changer, delve, unlock, unleash, revolutionize, supercharge, cutting-edge, next-level, elevate your, harness the power, it's important to note, it's worth noting, in conclusion, without further ado, look no further, at the end of the day, tapestry, testament to, a myriad of, dive in, deep dive, buckle up, synergy, paradigm shift, best-in-class, world-class, move the needle, we're excited to, we're thrilled, stay tuned, seamless, robust, holistic, empower, transformative.* Replace each with the specific thing you mean. Writing "faster to approve" beats calling something "seamless".

**Fact discipline.** Every number, percentage and direct quote must trace to `facts[]` (fetched verbatim) or the brief. Never invent a statistic, round a figure into a stronger one, or attach a quote to someone who didn't say it. If you can't verify something but the user wants it in, mark the sentence `[unverified]`. Lint turns that into a WARN, and the reviewer must resolve it before sending. Attribute in the sentence ("according to…", "on episode 112…"), not only through the link.

More examples and before/after rewrites: `writing-guide.md`.

## Step 5 — Lint, fix, render

Write the draft as `draft.json` (schema in `DATA_CONTRACT.md`), including `brief_topic_count` from Step 1 every time. Then:

```bash
python3 newsletter.py lint --draft draft.json --config config.json --brief brief.md
python3 newsletter.py render --draft draft.json --config config.json --format markdown
python3 newsletter.py render --draft draft.json --config config.json --format html --out newsletter.html
python3 newsletter.py check-html --file newsletter.html
python3 newsletter.py render --draft draft.json --config config.json --format text   # plain-text alternative
```

`lint` exits 1 on any FAIL, including "padding: N sections for M brief topics" when there are more sections than brief topics. If lint WARNs that `brief_topic_count` is missing, add the line it names to `draft.json` and re-run. Fix every FAIL and re-run. If FAILs remain after 3 passes, show the draft with them listed at the top. Over budget, lint names the section(s) to cut: fewest sections, then fewest words lost, then the later section. HTML is a 600px table layout with inline styles only, a hidden preheader and a table-cell button, and must pass `check-html` before handover. `--json` gives structured output. Bad input exits 2 with the file and field named.

## Step 6 — Post for review

One message, in this order:

1. `#### Newsletter draft — Week of <date>` with the numbered subject line options and their character counts, then the preheader.
2. The full newsletter in the configured `format` (markdown inline, with the HTML attached as a file when `format: html`).
3. A review note: lint result (PASS/WARN with any WARN rows), body words against the limit, which section to cut if it runs long, any `[unverified]` items, any source that couldn't be fetched, and the voice profile on the first run.

Then stop. A human copies it into the ESP.

## Guardrails

- **Never send the newsletter or schedule it,** even if a send-capable integration (ESP, Gmail, HubSpot) is connected. Always draft for human review.
- Never plagiarise. Commentary means a point of view, not a rewrite. Quote verbatim text only inside quote marks, with attribution.
- Never invent statistics, quotes or claims that aren't in the fetched sources or the brief.
- If the brief has fewer topics than `sections`, reduce the section count. Don't pad with filler.
- Subject lines: no misleading claims, no false urgency, no spam-trigger words.
- With `format: html`, the output must pass `check-html`: no `<div>` layout that breaks Outlook, no `<style>`/`<link>` or external stylesheets, no flex/grid, no scripts.
- Never choose a past-issue source or `cta_type` the user hasn't confirmed (Step 0).

## Automation recipe

In **Space settings → Automations**, add a weekly schedule (for example Thursday 09:00 for a Friday send) with the prompt "Draft this week's newsletter from [content queue]". New automations start disabled, so enable it. Scheduled runs only post a draft.

## Limits

- Lint checks figures and quotes against `facts[]` and the brief. It can't confirm that a fact quote really appears on the live page. That depends on the fetch in Step 1.
- Stance and clickbait detection are keyword heuristics. A WARN means "look at this", not proof of a problem.
- `check-html` is static. It doesn't render in Outlook or Gmail, so send a test from the ESP before scheduling.
- Rendering supports only `**bold**` and `[text](url)` in bodies, and no images. The ESP template owns logos, the unsubscribe link and the postal address.
- Padding is only caught when `brief_topic_count` is set in draft.json. Without it, lint can't know the brief had fewer topics (it WARNs instead), so always set it.
- Urgency detection is a phrase list: a bare topical word like "deadline" in a subject line is fine, but phrases like "deadline today" or "last chance" need a real `cta.deadline`.
- Word counts treat any whitespace-separated token containing a letter or digit as a word, so they may differ slightly from an ESP's count.

## Verification status

**Tested offline:** `bash run.sh` reproduces `expected_output.txt` byte for byte. `bash tests.sh` passes 18 tests covering bad JSON, missing fields, a blank `cta_type`, over-budget cut plans, reduced sections, padding (more sections than brief topics), a missing `brief_topic_count`, pure-ASCII scripts, unverified figures (marked and unmarked), false urgency (and topical "deadline" allowed), reply CTAs, HTML escaping, and check-html rejecting web-style markup. All sample data is fictional.

**Unverified against live systems:** fetching past issues from Drive, Beehiiv, Mailchimp or Kit; web fetch of real source pages; and how the rendered HTML looks in real email clients. Check these on first use.
