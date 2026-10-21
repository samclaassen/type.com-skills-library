# Writing the update: guidance for the agent

How to turn a period's meetings, metrics and last update into items for `build_update.py`. Adapted from three
MIT-licensed projects (see `NOTICE.md`); the rules on names, confidentiality, figures and forward-looking wording are
Type's and are stricter than the sources.

The founder's update exists to build trust, to make it easy for investors to help, and to make the founder think
clearly about the period. It is not a press release. Write in the founder's voice: plain, direct, first person
plural ("we"), no hype.

## The order of a standard update

The script prints these sections in this order and never drops one:

1. **TL;DR**: built by the script from the headline metrics, the first highlight, the first lowlight and the first
   ask. Put the item that defines the period first in its section.
2. **Key metrics**: the same metrics, in the same order, every period, each with its previous figure, change,
   source and as-of date. Runway is always in it. The script computes every figure and change; you only supply
   rows of data.
3. **Highlights**: up to five wins, most important first. A good highlight is specific and consequential: "the
   carrier-portal beta went live with a mid-market logistics customer" beats "launched new feature".
4. **Lowlights**: required. One to three things that went wrong, each specific, with what was learned and what is
   being done about it. Investors forgive misses; they do not forgive surprises. If a metric moved the wrong way
   (the script lists them), a lowlight should say why.
5. **Product**: what shipped and why it matters. Results only if measured, and measured figures go in the
   metrics data, not in the text.
6. **Customers & go-to-market**: logos, renewals, partnerships, pipeline changes in words. Report by count only
   through metrics; name a customer only if it is marked `public_ok`.
7. **Team & hiring**: hires who have started or signed, roles being hired for. No performance, pay, health, visa or
   departure details about named people; those are `personnel` items for the board, if anyone.
8. **Asks**: one to three asks an investor could answer from a phone: a named kind of introduction (role, company
   size, industry), a hire with role and location, or feedback on a specific decision. "Any intros welcome" gets
   nothing.
9. **What we said last time vs what happened**: every commitment from the previous update, quoted as it was written,
   with a true status: done, partial, missed, changed, blocked or dropped. No rewriting history: never soften the
   wording, move the date or swap the metric after the fact.

## Status words that carry information

| Kind | Use | Never |
| --- | --- | --- |
| Customer | signed, live, onboarding, in active conversation, sent with no reply | "in motion", "close to signing" |
| Hire | started, signed, offer extended, final round, recruiting | "basically done" |
| Product | shipped on a date | "coming soon" |

A forward-looking status ("on track for November") is only kept when the founder said it (see below).

## Phrases to delete on sight

"Thrilled to share", "excited to announce", "amazing month", "transformative", "strong momentum", "exciting
conversations", "numerous opportunities", "pipeline grew significantly". Each one spends the reader's attention and
says nothing. Numbers beat adjectives, and numbers come only from the metrics data.

## Bad news

State the fact in the first sentence of the item, then the cause, then what we are doing about it. Do not lead with
the mitigation, do not call a loss a "transition", and do not bury it under good news. When a favourable figure has
an unfavourable counterpart that changes its meaning, put both in the metrics table so the reader sees them side by
side.

## Rules the script enforces (and how to write so items survive them)

- **Every figure comes from the metrics data.** Write `{metric:mrr}`, `{metric:mrr.previous}` or
  `{metric:mrr.change}` instead of typing a number. Other digits (except years, dates, quarters such as Q3, version
  numbers and names such as SOC 2 or 3PL) and number words before a metric noun ("two new customers", "doubled")
  withhold the item. A figure someone mentioned in a meeting ("pipeline is about $300k") is not a metric: leave it
  out, or ask the founder for the real figure with its source and add it as a metrics row. A row's `source` names the
  system of record ("Stripe", "Finance sheet"); a row sourced to a meeting, call or notes is withheld.
- **Names only when public.** Write `{org:key}` or `{person:key}`. The script prints the name only for an entity
  marked `public_ok: true` and the descriptor otherwise. Ask the founder which customers, partners and people may be
  named; when unsure, the answer is no. Give every non-public entity a descriptor that does not identify it ("a
  mid-market logistics customer", not "our largest customer in Ohio"), and list nicknames and short forms as
  `aliases`.
- **Confidential items are tagged.** Anything about fundraising terms (valuation, term sheets, cap table, lead
  investors), legal matters (disputes, lawyers, regulators), personnel matters about identifiable people
  (performance, pay, health, leave, departures), or marked board-only gets `confidential`. Investor drafts leave it
  out; board drafts print it in a separate "Board only" section. The term scan also holds back untagged departure
  wording ("left the company", "stepped down"); if the founder wants a neutral departure in the investor update, they
  approve that exact item and you set `cleared_by_founder`.
- **No promises the founder didn't make.** "Will", "plan to", "on track", "next quarter" and similar wording need a
  `founder_stated` excerpt: the founder's own sentence from a meeting's notes or transcript, with their speaker
  label. The item must be that sentence word for word, not a paraphrase; if you want other wording, drop the
  forward-looking part. Questions ("Will we be profitable next year?"), conditionals ("If we close the round, we will
  be profitable.") and anything said only in chat don't count. Never turn a hope from a team meeting into a promise
  to investors.
- **Quotes need permission.** Only quote a customer or partner when the founder confirms the speaker agreed to be
  quoted in investor updates; set `permission` accordingly. A quote is one or more whole sentences from one speaker's
  turn, copied as written: no cut-off start or end, no "…", no speaker label.

## Length

A normal month reads in about two minutes. A quiet month should be short and say so; "nothing to report this period"
is a respected line. Do not pad.
