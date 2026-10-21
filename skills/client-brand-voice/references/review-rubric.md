# Client Draft Review Rubric

> Adapted from two upstream sources. First, Anthropic's `marketing/skills/brand-review`
> (Apache-2.0): the review dimensions, legal and compliance flags, severity
> scale, output format, style and terminology checklists. Second, Tribe AI's
> `brand-voice-enforcement` skill and its `voice-constant-tone-flexes.md`
> reference (MIT): the voice-constant / tone-flex model and conflict handling.
> Changes: every review is tied to one named client's
> `clients/<slug>/brand-voice.md`; findings carry line numbers and merge in
> the helper script's mechanical hits; the output is a draft for the account
> lead; there is no fallback to another client's guidelines.

## Before you start

- Name the client and the guidelines file, including its `updated` date and
  `status`. If the status is `draft`, say so in the review header.
- Number the draft's lines exactly as the helper does (line 1 is the first
  line of the text file) so script hits and your findings share one line
  index.
- Run `scripts/check_voice_rules.py` first if the guidelines have a
  `voice-rules` block. Its hits are facts. Your job is to judge the rest.

## Voice constant, tone flexes

**Voice** is who the client's brand is: its personality and values, set by
the client's We Are / We Are Not table. It doesn't change between channels.
**Tone** is how the brand speaks in a given moment. It flexes along three
dimensions, using the client's tone-by-context matrix:

| Dimension | High | Medium | Low |
|---|---|---|---|
| Formality | Complete sentences, structured (proposals, RFPs) | Professional but conversational (most email, blog) | Contractions, personality (social, internal) |
| Energy | Active verbs, short sentences (launches, hooks) | Steady and balanced (follow-ups, case studies). "Warm" is an alternative: empathetic, for support and bad news | Measured (legal, policy, sensitive topics) |
| Technical depth | Specs, terminology (technical buyers) | Terms explained (mixed audiences) | Outcomes, no jargon (executives, cold outreach) |

Common mistakes to avoid:
1. Expecting every voice attribute at full strength in every sentence. Let the
   2 or 3 most relevant ones lead.
2. Mixing up voice and tone. "Make it casual" changes tone, not the brand's
   personality.
3. Enforcing rules so rigidly the copy stops reading naturally. The guidelines
   are principles, not a checklist.
4. Ignoring the audience. The same voice reads differently to a CTO and to a
   consumer.

## Dimensions to check (with the client's guidelines)

### Voice and tone
- Does the draft reflect the client's We Are attributes? Does it cross any We
  Are Not boundary? Quote the line.
- Is the tone right for this content type and audience, according to the
  client's matrix?
- Does the voice shift partway through?

### Terminology and language
- Preferred terms and avoided terms. The helper catches exact phrases.
  You catch variants, plurals, and misuse in context.
- Product names, feature names, and branded terms: capitalization,
  formatting, and whether to use "the" before a product name.
- Jargon level for the audience. Acronyms defined on first use.
- Inclusive language: gender-neutral wording, no ableist terms, no idioms
  that don't translate. Prefer "simple" to "easy".

### Messaging pillars
- Does the draft align with the client's pillars and value proposition?
- Are the claims consistent with the client's approved messaging? Does the
  draft reinforce or contradict its positioning?

### Style guide compliance
Check the client's stated choices: Oxford comma, heading case, contractions,
em dash spacing, numbers (spell out 1 to 9 or use numerals), % vs. "percent",
date and time formats, list punctuation, descriptive link text (never "click
here"), exclamation policy, ellipses, ALL CAPS, and emoji by channel.

### Open questions
If the draft touches an unresolved open question in the client's guidelines,
note it. Apply the recommendation from the guidelines unless the user decides
otherwise.

## Without guidelines for this client (generic review)

Label it: "Generic review — not checked against <Client> guidelines." Check
clarity (is the main message in the first paragraph? are sentences concise?
is the structure logical?), consistency (the same term throughout, consistent
formatting), and professionalism (typos, tone that fits the audience,
supported claims). Never borrow another client's guidelines to fill the gap.

## Legal and compliance flags (always checked)

- **Unsubstantiated claims:** "best", "fastest", "only", "guaranteed"
  without evidence or qualification
- **Missing disclaimers:** financial or health claims, or guarantees
  (check `client.json` → `notes` for client-specific legal review rules)
- **Comparative claims:** comparisons with competitors that could be
  challenged
- **Regulatory language:** content in regulated categories (finance,
  healthcare, alcohol, and so on) that needs compliance review
- **Testimonial issues:** quotes or endorsements without attribution or
  disclosure
- **Copyright concerns:** text closely paraphrased from other sources

These are prompts for review by the account lead or legal. They are not legal
advice.

## Severity

- **High:** contradicts the client's voice, carries compliance risk, or
  seriously undermines the messaging. Banned-phrase hits default to High.
- **Medium:** inconsistent with the guidelines but not damaging. Preferred-term,
  sentence-length, and exclamation hits default to Medium unless the
  guidelines say otherwise.
- **Low:** a minor style or preference issue.

## Output format

```markdown
**DRAFT review for the <Client> account lead**
Client: <Client> (<slug>) · Guidelines: clients/<slug>/brand-voice.md (updated <date>, status <status>) · Draft: <file or "pasted text">

### Summary
- Overall alignment with <Client>'s voice: <one line>
- Strengths: <1-2 sentences>
- Most important fixes: <1-2 sentences>
- Mechanical check: <N violations | clean | not run: reason>

### Findings
| Line | Issue | Rule / guideline | Severity | Suggested edit |
|------|-------|------------------|----------|----------------|
| 3 | "customers" | prefer: members | Medium | "…ready for members who want…" |

### Suggested rewrites (top 3-5)
**Line N — before:** …
**After:** …
**Why:** <attribute / rule it restores>

### Legal / compliance flags
- Line N: <flag> — recommended action

### Open questions touched
```

Then ask whether to apply all the suggested edits, fix only the
high-severity ones, or review another draft **for the same client**. Don't
change the user's file until they approve.

## When a request conflicts with the guidelines

Explain the conflict, recommend a path, and offer three options: follow the
guidelines strictly, adapt them for this context (and note it), or override.
If the user wants to override, note in the review that the override is theirs
and should be confirmed with the account lead.
