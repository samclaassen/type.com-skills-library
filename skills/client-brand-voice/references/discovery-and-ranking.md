# Client-Scoped Discovery and Source Ranking

> Adapted from Tribe AI's brand-voice plugin (MIT): `skills/discover-brand/SKILL.md`,
> `references/search-strategies.md` and `references/source-ranking.md`. Main
> change: discovery used to search a whole workspace for "the" brand. Now it
> searches only the sources the user scopes to one named client, and leaves
> out anything that might belong to another client.

## 1. Scope before you search

In an agency workspace, the query "brand guidelines" returns every client's
brand book. So before any search, ask one short question:

> "Which sources belong to **<Client>**? For example: a Drive or SharePoint
> folder, a Notion teamspace or page, a Confluence space, a Slack channel,
> call recordings with them, files you'll upload, or their public website."

Record the answer as the **scope** in the discovery report and in the
guidelines' Generation Metadata.

### Classify what's in scope

**Document sources** (where brand guides, style guides, templates and decks
live): a client folder in Google Drive, SharePoint/OneDrive or Box; a Notion
teamspace; a Confluence space; uploaded files; the client's website.

**Supplementary sources** (useful for patterns, but not where brand docs
live): a client Slack channel, Gong or Granola calls with the client, a
client Figma project.

- **No document sources in scope:** warn the user and continue. "Brand
  guidelines usually live in documents. With only <sources>, confidence will
  be lower. Is there a folder with <Client>'s brand book or style guide?"
- **Only one source in scope:** warn that one source can't be checked
  against another, so confidence scores will be lower.
- **A scoped platform isn't connected, or returns permission errors:** note
  the gap and continue with the other sources. Don't widen the scope to make
  up for it.

## 2. Query patterns, confined to the scope

Run every query inside the folder, space, channel, or project the user named,
and put the client's name in it. Examples:

| Platform | How to confine | Queries |
|---|---|---|
| Google Drive / SharePoint / Box | Search within the named folder (parent or folder filter) | "<Client> brand guidelines", "style guide", "messaging framework", "tone of voice", "brand book", "pitch deck", "email templates" |
| Notion | Search within the named teamspace or page tree. Notion AI search can federate to other tools, so check where each hit actually lives | "<Client> brand voice", "messaging", "launch messaging", "social media guidelines" |
| Confluence | Limit to the named space key | "brand style guide", "voice and tone", "content standards", "editorial guidelines" |
| Slack | Limit to the named channel(s) | "tone", "voice", "wording", "rebrand", "tagline", "approved copy", "don't say" |
| Gong / Granola | Calls where the client's people attended, or tagged with the client | the client's messaging pillars, objections, how the client describes itself |
| Figma | The named client project | "brand kit", "design system", "voice and tone", "content guidelines" |
| Website | The domain in `client.json` → `website` | About, product pages, recent blog posts, press releases |

Tips carried over from upstream:
- Track source URLs so the same document found on two platforms is counted
  once. Keep the most recently updated copy.
- For operational, conversational, and contextual sources, focus on the last
  12 months. Look further back only for explicit brand documents.
- If a scoped platform returns nothing, try broader queries *inside the same
  scope* ("marketing", "sales"). Record the gap either way. An empty result
  still tells you something.

### Exclusion rule

Leave a hit out, and list it under "Excluded as possibly another client's",
when any of these is true:
- it names a different client or a different brand as its subject
- it sits in a shared or unlabelled location and nothing ties it to this
  client
- it is the agency's own brand, pitch, or credentials material

When unsure, leave it out and ask. A missing source lowers confidence, but a
leaked source puts the wrong brand's voice in the guidelines.

## 3. Source categories

| Category | What it is | Trust weight |
|---|---|---|
| AUTHORITATIVE | The client's official brand book or style guide; approved by the client's brand or leadership team; versioned or dated; referenced as "the brand guide" | 1.0 |
| OPERATIONAL | The brand in practice: the client's live templates, playbooks, email sequences, deck templates, approved campaign copy | 0.8 |
| CONVERSATIONAL | Implicit voice: calls, meeting notes, Slack threads about wording decisions | 0.6 |
| CONTEXTUAL | Background: design files without guidance, competitor analyses, product docs | 0.3 |
| STALE | Superseded versions, pre-rebrand material, archived or deprecated docs, non-authoritative material over 2 years old | 0.1 (flag for review; don't rely on it) |

Copy the agency wrote for the client counts as OPERATIONAL only if the client
approved it. Otherwise it is CONTEXTUAL at most, because it shows how the
agency writes, not how the client does.

## 4. Ranking

```
final_score  = recency×0.30 + explicitness×0.25 + authority×0.20
             + specificity×0.15 + consistency×0.10
ranked_score = final_score × category_trust_weight
```

- **Recency:** updated in the last 6 months = 1.0, last year = 0.7, last 2
  years = 0.4, older = 0.1. When sources conflict, the newer one wins unless
  the older one is explicitly the official guide.
- **Explicitness:** explicit brand instructions = 1.0, documented tone
  guidance = 0.7, implicit patterns in templates = 0.4, inferred from
  conversation = 0.2.
- **Authority:** the client's brand team or C-suite = 1.0, the client's
  marketing leadership = 0.7, team leads = 0.4, individual contributor or
  unknown = 0.2.
- **Specificity:** specific rules with examples ("use 'members' not
  'customers'") = 1.0, detailed guidance = 0.7, general principles = 0.4,
  abstract values = 0.2.
- **Consistency:** found in 3+ independent sources for this client = 1.0,
  in 2 = 0.7, in 1 = 0.4, contradicted = 0.1.

**No AUTHORITATIVE source for this client:** adapt the weights (OPERATIONAL
0.9, CONVERSATIONAL 0.85, CONTEXTUAL 0.4, STALE 0.2). Raise explicitness for
conversational patterns from 0.2 to 0.5 and for template patterns from 0.4 to
0.6. Put this note in the report: "No formal brand guidelines found for
<Client>. Scoring adapted to weight conversational and operational sources
higher."

**Recency cutoffs at deep fetch:** AUTHORITATIVE sources have no cutoff
unless superseded. For other categories, leave out anything older than 12
months, unless the category has nothing newer. In that case, include the
single most recent source and flag it. STALE sources appear in the report only.

### Triage

- **Deep fetch (top 5 to 15):** ranked score above 0.5, every AUTHORITATIVE
  source, and at least one source per category and per scoped platform if
  available.
- **Flag for review:** conflicting sources, STALE sources teams may still
  use, and sources that are highly specific but low in authority.
- **Exclude:** ranked score below 0.1, irrelevant hits, duplicates, and
  anything that meets the exclusion rule above.

## 5. Discovery report (per client)

```markdown
# Discovery Report — <Client> (<slug>)
> DRAFT for the <Client> account lead.

- Scope searched: <folders/spaces/channels the user named>
- Sources found / deep-read: N / M
- Excluded as possibly another client's: <list or "none">
- Coverage gaps: <platforms not connected, permission errors, empty results>

## Ranked sources
| # | Source | Location | Category | Updated | Score |

## Key brand elements found
## Conflicts
## Open questions (each with a recommendation: confirm or override)
```
