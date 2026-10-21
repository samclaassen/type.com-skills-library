# Writing guide — B2B newsletter

Extended guidance and before/after rewrites for SKILL.md Steps 3-4. The examples use the
fictional Harborline sample from the bundled sample files (`sample_draft.json`, `sample_brief.md`).

## Sections: a take, not a summary

A summary tells the reader what the source says. A take tells them what to *do* or *believe*
because of it. Build each section in this order:

1. **What happened, attributed.** One or two sentences with the figure or quote and who said it.
2. **The take.** Disagree, rank, predict, or recommend. Say why it matters to `audience` in particular.
3. **The action.** One concrete thing the reader can do this week. If the voice has a recurring
   structure (for example "**Steal this:**"), use it here.

Before (summary, 41 words):
> The Studio Ops Collective released its 2026 benchmark. It surveyed 310 agencies about
> operations. Scope creep was named the top margin leak by 58%. The report also covers
> pricing, tooling and hiring. You can read the full report at the link below.

After (take):
> The Studio Ops Collective surveyed 310 agencies for its 2026 benchmark, and 58% called scope
> creep their biggest margin leak. The more useful finding sits further down: only one in four
> agencies attaches hours to a change request. That's the gap worth closing. Scope creep isn't
> a client behavior you can stop. It's a pricing habit you can change.

Checks before moving on: Could a reader disagree with this section? If not, it has no take.
Does the section still make sense with the link removed? If not, you're summarising.

## Headlines

Descriptive, specific, sentence case, 70 characters or fewer. A reader who scans only the
headlines should learn something.

| Weak | Better |
| --- | --- |
| Podcast | Why one studio COO stopped publishing utilization targets |
| Big news! | Capacity Forecast shows who's overbooked six weeks out |
| You won't believe this stat | Scope creep is still the biggest margin leak for agencies |

## Openers

2-3 sentences. Name the thread that ties the issue together, as a claim.

- Bad: "Welcome to this week's edition of The Ops Desk! We've got a packed issue for you."
- Bad: "In today's fast-paced agency world, staying on top of operations is more important than ever."
- Good: "Every expensive problem in an agency was cheap a few weeks earlier. This week is about
  seeing them while they're cheap."

## CTA by type

| `cta_type` | Do | Don't |
| --- | --- | --- |
| product | Name the feature and the job it does: "Open Capacity Forecast and fix one week before it becomes a client conversation." | "Check out our amazing new feature!" |
| content | Name the resource and the one takeaway: "The 12-question SOW checklist we use with every new client." | "Read our latest blog post." |
| event | Name, date, time zone, and what they leave with: "Ops roundtable, Oct 9, 11:00 ET: three agencies walk through their capacity plans." | "Don't miss our webinar!" |
| reply | One real question the sender will read: "What's the one report you'd delete if you could? Hit reply." | "Let us know your thoughts!" |

Every figure in a CTA (dates, prices, counts) must also be in the brief.

## Subject lines

Pick one lever per line:

- **Specificity:** a concrete noun or number from the issue. "58% of agencies leak margin the same way".
- **Curiosity:** an open loop the issue really closes. "Why one COO stopped publishing utilization".

Both in one line ("The 1 weird metric 58% of COOs hide") reads as clickbait. Write
`subject_line_count` options with different levers, so the user is actually choosing. Under 50
characters, because mobile clients cut off around 35-45, so put the key words first.

Never: ALL CAPS (beyond real acronyms), emoji (unless `allow_emoji`), "RE:"/"FW:", "free",
"guaranteed", "act now", "last chance" without a real deadline, or anything the body doesn't deliver.

## Preheader

Extends the subject line, never repeats it. 100 characters or fewer. Good: "Capacity Forecast is
live, plus what 310 agencies say about scope creep." Bad: an empty preheader. Most clients then
show "View in browser" or the first line of the body.

## Fact discipline in practice

- Copy the sentence that contains the figure from the fetched page into `facts[]` *before* you
  write the section. Then write the section from `facts[]`, not from memory.
- Keep the source's precision. "23%" stays 23%. Don't turn it into "nearly a quarter" and then
  "a quarter". Don't widen the claim: if 41 beta agencies saw it, don't write "agencies see".
- Attribute in the sentence ("according to", "told Agency Hours", "the benchmark found").
- A number you can't trace: cut it, or mark the sentence `[unverified]` and flag it in the review note.
- Quotes: only verbatim, only inside quote marks, and only with the speaker named.

## Cutting to length

If lint reports over budget, cut whole sections first, using lint's suggestion. Then tighten
within sections. Delete the sentence that restates the headline, any sentence starting with
"This means that", and doubled adjectives. Don't cut the attribution or the take.

## Banned phrases: what to write instead

| Instead of | Write |
| --- | --- |
| game-changer, revolutionize | what changes, in numbers or steps: "cuts the Monday status call" |
| unlock, unleash, empower | the verb that actually happens: "see", "price", "book", "ship" |
| seamless, robust, holistic | the specific property: "no setup", "handles 80 projects", "covers billing and capacity" |
| delve into, deep dive | "look at", or just start the analysis |
| it's important to note | delete it and state the thing |
| in today's fast-paced world | delete it. The reader knows what year it is. |
| we're excited to announce | the announcement itself |
