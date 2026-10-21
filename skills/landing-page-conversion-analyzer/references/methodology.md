# Methodology

Every statistical choice here, and why the obvious alternative is wrong. Written for
whoever rebuilds this in another runtime and has to decide whether a shortcut is safe.

---

## 1. The model never reads the cube

A deterministic pass computes every slice and emits ~15–25 candidate rows. The language
model reads only the survivors.

This is not a cost optimisation, it is a correctness one. A model asked to scan thousands
of cells will find patterns in noise, and it will find *different* patterns on different
runs. The scan has to be reproducible; the narrative does not.

The public example ships its input, configuration, raw output, and reproduction steps.
Timing is specific to the machine and dataset; no production performance claim is made.

---

## 2. Hierarchical scan, not brute force

Top-down: site → one-way margins (landing page, source, medium) → two-way cells, opened
only beneath a margin that moved or above a revenue-share threshold → three-way beneath
those.

**Why not test everything.** Two reasons, and the statistical one matters more than speed.
Every additional test consumes false-discovery budget. Testing 4,000 cells to find the 24
that could ever have produced a signal means the correction has to be 160x harsher, and
real findings die with the noise.

The scanner limits comparisons through configurable volume and revenue-share gates.
Inspect scan_stats and untested_but_material to understand the coverage on your data.

---

## 3. Benjamini-Hochberg, not Bonferroni

Bonferroni controls the probability of *any* false positive. Across thousands of cells
that is so conservative it rejects everything real along with the noise, and the tool
reports "no anomalies" forever.

BH controls the *proportion* of findings that are false. At q=0.10, roughly one finding in
ten is expected to be noise — a contract an operator can actually reason about.

---

## 4. Day-of-week matched baselines

A current window containing two Mondays and no Saturday shows a shift that is purely
calendar. The baseline rate is re-weighted to the current window's weekday composition
before comparison — comparing Tuesdays to Tuesdays, without fitting a model to anything.

On a clean 7-day current window this is a no-op, which is exactly why 7 is the default.
It earns its keep when someone sets `current_days: 3`.

---

## 5. Shift-share decomposition — the load-bearing piece

Any change in a blended rate splits into three parts:

```
within       Σ wᵢ_base × (rateᵢ_cur − rateᵢ_base)      components converted differently
mix          Σ (wᵢ_cur − wᵢ_base) × rateᵢ_base         shares moved, rates did not
interaction  Σ (wᵢ_cur − wᵢ_base) × (rateᵢ_cur − rateᵢ_base)
```

When `mix` dominates, the blended rate moved because traffic composition changed, and no
individual component converted any differently. This is Simpson's paradox, and it is the
normal state of affairs at any brand that scales paid media.

**Two decompositions, not one.** Each cell is decomposed across the available control
dimensions (device, new vs returning) *and* across its natural child dimension — for a
landing-page cell that is `utm_source`. The stronger effect wins.

This was a real bug during development: with `device` present, only the device
decomposition ran, device mix had not moved, and so source-mix shifts inside a landing page
were reported as page problems. Five of seven findings in a test run were mislabelled that
way. Both decompositions are required.

**Mix-dominated findings are reported in a separate list and never ranked with real ones.**
A composition effect can be larger than a genuine conversion change. Ranking them
together can direct the operator to the wrong problem.

---

## 6. Funnel localisation

A conversion-rate drop is not actionable; a *step* is. The same headline drop means
opposite things:

| Where it broke | What it is | Whose problem |
|---|---|---|
| `begin_checkout → purchase` | payment, shipping, a checkout bug | engineering |
| `add_to_cart → begin_checkout` | cart friction, unexpected cost | product / UX |
| `view_item → add_to_cart` | price, stock, merchandising | merchandising |
| `sessions → view_item` | page load, message match, traffic quality | paid media |

The bundled showcase plants a change at checkout and verifies that the emitted
worst transition is checkout to purchase while earlier transitions remain unchanged.

---

## 6b. Root-cause attribution — one problem, one finding

When one cell genuinely breaks, every aggregate containing it moves. A planted break in a
single cell produced **25 separate notifications** in testing: the cell, each source
inside it, and then most landing pages and sources at site level, none of which had
anything wrong. A team that receives that once mutes the tool.

The method is what an analyst does by hand: take the broken cell out, and ask whether the
rest still looks broken. Peel the strongest finding, re-test everything else with it
removed, and anything no longer clearing the effect-size bar was never its own problem.

**Choosing the root is the hard part, and every obvious heuristic fails.** Largest dollars
picks a diluted symptom inside a huge aggregate. Largest rate move picks the narrowest
cell — if something is down 30% across the board, one slice inside it is down 43% by
chance and peeling that explains nothing. Broadest-first picks a site-wide aggregate that
is itself a symptom. So score against the objective directly: how many other findings does
peeling this one account for, approximated by containment, restricted to cells whose rate
move is within 60% of the strongest.

Two ordering bugs found in testing: a greedy peel lets a finding tested before its parent
survive as "independent" (fixed by a final consistency pass against the fully peeled data),
and findings past the peel budget were returned untouched, making "rank low enough to be
skipped" the cheapest route to being reported as independent — precisely backwards.

Nothing is deleted. Explained findings stay in the payload, tiered to `context`, labelled
with the root and with what they move to once it is removed.

---

## 6c. Evaluation and notification are separate decisions

Every cell above the volume floors is evaluated identically. A separate, brand-editable
policy assigns `notify` / `flag` / `context`.

Whether something is TRUE is a statistical question with one right answer. Whether it
deserves a push is a business preference that belongs to the brand, changes by season, and
differs between an in-house team and an agency watching twelve accounts. An earlier version
folded the second into the first with a revenue floor inside the scan — which meant a small
but genuinely collapsing slice never appeared anywhere at all.

---

## 7. Sensitivity — what the brand can and cannot see

Minimum detectable effect per level, at α=0.05 two-sided and 80% power:

```
MDE_abs = 2.80 × √( p(1−p) × (1/n_cur + 1/n_base) )
```

**Why this is in the product and not a footnote.** "No anomalies found" and "no anomalies
detectable" are different statements, and a tool that cannot tell them apart teaches its
users to trust a silence that means nothing.

The baseline output calculates sensitivity from the supplied volume. Widening the
comparison windows can improve sensitivity, but also delays detection and requires
more stable history. Choose windows for your own use case.

---

## 8. Equivalence, not non-significance, for alias detection

To decide whether `fb` and `facebook` behave like the same source, the tempting test is
"their conversion rates are not significantly different." That test is wrong, and it fails
in the direction that matters.

At 900k sessions per source, 2.33% vs 2.51% is overwhelmingly significant while being
practically identical. A significance gate rejects exactly the pairs it should catch, and
gets *more* wrong as the account gets bigger.

The right question is equivalence: are the rates inside a narrow relative band. Default
20%.

String similarity and behavioural equivalence catch different things and both are needed:

| Detector | Catches | Structurally cannot catch |
|---|---|---|
| Canonical-form clustering | `Facebook`/`facebook`, `facebook.com`/`m.facebook.com` | anything that is not a spelling variant |
| Edit distance + prefix | `facebook`/`Facebook_Ads` | `fb` — an initialism shares no prefix |
| Behavioural equivalence | `fb`/`facebook`, `ig`/`instagram` | genuinely different sources that behave alike |

**False-positive guard.** Values of equal length differing only in the final character —
`partner-a` / `partner-b`, `campaign1` / `campaign2` — are excluded. That trailing character
is how humans enumerate genuinely different things, and string similarity calls them
aliases every time. A confident wrong suggestion costs more credibility than a missed one.

Nothing is ever merged. Confirmed groupings go in config, written by a person.

---

## 9. Freshness, tracking breaks, and regime shifts

**No lag model, by design.** Schedule the run for a time when the brand's data has settled
and refresh on demand. `config.schedule.settles_by` records the brand's own observation and
is used only to recommend a schedule and warn on an early run — it never changes a
calculation. Two automatic guards remain, because no schedule protects against a late or
mid-write export.

**Restatement.** A day already reported on can gain late-arriving events and change. The
freshness guards do nothing about that: they stop you analysing data that is obviously
incomplete, not data that quietly moved afterwards. Each run fingerprints every day's
totals; the next run re-reads and names what changed. Silent when nothing moved. This
requires the runtime to RE-READ a trailing window rather than appending — you cannot notice
a number changing if you never look at it twice.

**Freshness.** The most recent day or two of analytics is incomplete, and incomplete data
looks exactly like a catastrophe. Two independent guards: the runtime's declared
`complete_through`, and a check on the share of sessions in unresolved source states. A day
whose unresolved share spikes above its own trailing norm is excluded regardless of what
the runtime claimed. An export can pass its declared cutoff while unresolved-source volume still signals
incomplete processing.

**Tracking breaks.** A conversion step that collapses while traffic holds is a broken tag
far more often than a real collapse. Scanned across the **entire** loaded range, not just
recent days — a break inside the *baseline* window is more dangerous than one in the
current window, because it lowers the bar and hides everything that goes wrong afterwards.
Detected days are excluded from every window. This was found by test: a planted break sat
in the baseline and was silently masking the current-window signal until the scan was
widened.

**Regime shifts.** When current-window volume runs far from its baseline — peak season, a
migration — the trailing baseline compares peak to pre-peak and flags the entire site every
day. Detected on volume alone, with no calendar knowledge, and the baseline switches to the
same period last year, offset by 364 days so weekday alignment is preserved. If there is
not enough history for that, it says so and marks the run provisional rather than
pretending.

---

## 10. Deliberate omissions

**No CVR benchmarks or standards.** Every judgement is the brand against its own history.
Cross-brand conversion benchmarks are dominated by category, price point and traffic mix,
and quoting one invites a decision it cannot support.

**No cost, CAC or ROAS.** A later phase. Adding spend to the contract turns a CVR move into
a CAC move, which is what a paid team acts on — but it is a different data join and a
different set of failure modes.

**No causal claims.** The engine reports what moved, where in the funnel, and how much money
is attached. It does not know *why*. Hypotheses are labelled as hypotheses.
