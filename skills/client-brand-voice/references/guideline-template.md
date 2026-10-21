# Client Brand Guidelines Output Template

> Adapted from Tribe AI's brand-voice plugin (MIT). Changes: one file per client at `clients/<slug>/brand-voice.md`, a draft banner and client line in the metadata, a status field, and a machine-checkable `voice-rules` block read by `scripts/check_voice_rules.py`.

The complete structure for one client's generated brand voice guidelines. Fill each section from sources that belong to this client only. The following is a raw markdown template — copy and fill the sections below and save it at `clients/<slug>/brand-voice.md` after the user approves. Omit sections with no data rather than including empty placeholders — except the draft banner, Generation Metadata and Machine-Checkable Rules block, which are always present.

# [Client Name] Brand Voice Guidelines

> DRAFT for the [Client Name] account lead. Generated from client-scoped sources; not approved by the client.

## Generation Metadata
- Client: [client-slug] ([Client Name from client.json])
- Status: [draft | approved by <account lead> on <date>]
- Created: [date]
- Version: [N] (incremented from previous if updating)
- Replaces: [previous version date, if applicable]
- Sources: [list of this client's documents and/or conversations analyzed, with the scope the user named]
- Excluded as possibly another client's: [list, or "none"]
- Documents processed: [N]
- Conversations analyzed: [N]
- Discovery report used: [Yes/No]
- Overall confidence: [High/Medium/Low]

---

## Executive Summary

[2-3 paragraph overview of the brand voice. What makes this brand distinctive?
What is the core personality? How should content feel to the reader?]

---

## We Are / We Are Not

The foundational brand identity anchor. Voice is constant — it doesn't change by channel or audience.

| We Are | We Are Not |
|--------|------------|
| **[Attribute 1]** — [brief description] | **[Counter 1]** — [what to avoid] |
| **[Attribute 2]** — [brief description] | **[Counter 2]** — [what to avoid] |
| **[Attribute 3]** — [brief description] | **[Counter 3]** — [what to avoid] |
| **[Attribute 4]** — [brief description] | **[Counter 4]** — [what to avoid] |
| **[Attribute 5]** — [brief description] | **[Counter 5]** — [what to avoid] |

### Voice Attributes Detail

For each "We Are" attribute:

#### [Attribute 1]: [Name]
- **What it means**: [detailed description]
- **How it shows up**: [specific behaviors in content]
- **What to avoid**: [the "We Are Not" counterpart, expanded]
- **Evidence**: [source quote or pattern, with citation]
- **Confidence**: [High/Medium/Low]

[Repeat for each attribute]

---

## Brand Personality

- **Archetype**: [e.g., "The Expert Friend" — technically deep but never condescending]
- **If our brand were a person**: [brief personality sketch]
- **Core values expressed in voice**: [list]

---

## Messaging Framework

### Primary Value Proposition
[Core value statement — one sentence]

Variations observed:
- "[Variation 1]" (Source: [doc])
- "[Variation 2]" (Source: [doc])

### Key Message Pillars

1. **[Pillar Name]**
   - Core idea: [one sentence]
   - When to use: [contexts]
   - Example phrasing: "[quote]"
   - Frequency in conversations: [X]% (if transcript data available)
   - Effectiveness: [High/Medium/Low] (if outcome data available)

2. **[Pillar Name]**
   [same format]

3. **[Pillar Name]**
   [same format]

### Competitive Positioning
- vs. [Competitor A]: [how we differentiate]
- vs. [Competitor B]: [how we differentiate]
- vs. Status Quo: [why change is worth it]

---

## Tone-by-Context Matrix

Voice is constant. Tone flexes by context. These three dimensions adjust:

| Context | Formality | Energy | Technical Depth | Key Principle |
|---------|-----------|--------|-----------------|---------------|
| Cold outreach | Medium | High | Low | Hook fast, earn attention |
| Discovery calls | Medium | Medium-High | Medium | Ask more than tell |
| Demo / presentation | Medium-High | High | High | Show, don't just describe |
| Enterprise proposal | High | Medium | High | ROI and precision |
| Follow-up email | Medium | Medium | Low-Medium | Add new value each touch |
| Social media | Low-Medium | High | Low | Brevity and personality |
| Customer success | Medium | Warm | Medium | Empathy and competence |
| Internal comms | Low | Medium | Varies | Authentic, less polished |

### Context-Specific Guidelines

#### Cold Outreach
- **Overall tone**: [description]
- **Opening approach**: [what works]
- **Do's**: [list]
- **Don'ts**: [list]
- **Example**: "[sample opening line]"

#### Discovery Calls
[same structure]

#### Proposals & RFPs
[same structure]

#### Social Media
[same structure]

---

## Terminology Guide

### Must-Use Terms
| Term | Usage | Instead Of | Example |
|------|-------|------------|---------|
| [term] | [when/how to use] | [what it replaces] | "[in a sentence]" |

### Preferred Terms
| Term | Usage | Example |
|------|-------|---------|
| [term] | [guidance] | "[in a sentence]" |

### Avoid These Terms
| Term | Reason | Alternative |
|------|--------|-------------|
| [term] | [why to avoid] | [what to use instead] |

### Never-Use Terms
| Term | Reason |
|------|--------|
| [term] | [why this is prohibited] |

---

## Language That Works

Ranked by effectiveness (from conversation analysis, if available):

### Top Phrases
1. **"[Phrase]"** — Context: [when to use], Why it works: [analysis]
2. **"[Phrase]"** — Context: [when to use], Why it works: [analysis]

### Questions That Engage
1. **"[Question]"** — Leads to: [typical outcome]
2. **"[Question]"** — Leads to: [typical outcome]

### Objection Handling Patterns
1. **Objection**: "[common objection]"
   **Response pattern**: "[how top performers respond]"
   **Why it works**: [analysis]

---

## Language to Avoid

### Anti-Patterns
1. **"[Phrase/pattern]"** — Problem: [what happens], Better: "[alternative]"
2. **"[Phrase/pattern]"** — Problem: [what happens], Better: "[alternative]"

---

## Content Examples

### Excellent Examples
[Full example with annotation explaining what makes it on-brand]

### Examples to Avoid
[Full example with annotation explaining what's off-brand and how to fix it]

---

## Confidence Scores

| Section | Confidence | Basis | Sources |
|---------|------------|-------|---------|
| Voice Attributes | [H/M/L] | [why] | [N sources] |
| Messaging Framework | [H/M/L] | [why] | [N sources] |
| Tone Matrix | [H/M/L] | [why] | [N sources] |
| Terminology | [H/M/L] | [why] | [N sources] |
| Language Patterns | [H/M/L] | [why] | [N sources] |

---

## Open Questions for Team Discussion

### High Priority (blocks guideline completion)
1. **[Question Title]**
   - What was found: [description of ambiguity]
   - Agent recommendation: [suggested resolution]
   - Need from you: [specific decision]

### Medium Priority (improves quality)
[same format]

### Low Priority (nice to have)
[same format]

---

## Data Gaps & Recommendations

[What's missing and specific actions to fill gaps]

- [ ] [Gap 1]: [recommendation to fill it]
- [ ] [Gap 2]: [recommendation to fill it]

---

## Machine-Checkable Rules

Exactly one fenced `voice-rules` block, in the format defined in `DATA_CONTRACT.md`. Include only rules that a source states explicitly or that the user confirmed; keep inferred preferences in the prose sections above. Mirror the Terminology Guide's "Never-Use" and "Must-Use ... Instead Of" rows here.

```voice-rules
client: [client-slug]
updated: [YYYY-MM-DD]
status: draft
ban: [never-use term]
prefer: [must-use term] | [term it replaces]
max_sentence_words: [N, only if the client has a stated limit]
exclamations: [none | allow | max N]
```

---

## Appendix: Sources

| # | Source | Platform | Type | Date | Key Sections Used | Confidence |
|---|--------|----------|------|------|-------------------|------------|
| 1 | [title] | [platform] | [AUTHORITATIVE/OPERATIONAL/etc.] | [date] | [sections] | [H/M/L] |

---

## Template Usage Notes

- Omit sections with no data rather than including empty placeholders
- If transcript data is unavailable, omit the "Language That Works" and "Language to Avoid" sections
- If only documents are available (no transcripts), note this in metadata
- "We Are / We Are Not" table should have minimum 4 rows, ideally 5-7
- Tone matrix should cover at minimum: cold outreach, proposals, social media
- All examples must have source attribution
- Every open question must include an agent recommendation
- Never copy terminology, examples or rules from another client's guidelines, even as a starting point
- `status` stays `draft` until the account lead approves; only they change it to `approved`
