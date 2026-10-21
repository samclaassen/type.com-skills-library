# Rubric guide: building and using an interview rubric

How to write the per-role rubric this skill scores against, and how to read an
interview inside it. Adapted from Anthropic's `knowledge-work-plugins` hiring
skills (Apache-2.0; see `NOTICE.md` for the sources and the changes made).

---

## The rule

**Score only against the rubric.** The competencies the job needs, each with a
behavioural anchor for every score, agreed by the hiring team. Nothing else
enters the score.

Two reasons, and both matter. Decisions that turn on age, sex, race, national
origin, disability, religion and other protected characteristics are unlawful
in most places you will hire. And it produces better hires: every point spent
on how someone came across is a point not spent on whether they can do the job.

**Write the rubric before the interviews.** Once you have met the candidates,
a rubric gets written to fit whoever impressed you, which is backwards and
impossible to defend later. If there is no rubric, build one and stop; do not
score the interview you have against one invented afterwards.

---

## Design principles

1. **Structured:** the same competencies and core questions for every candidate for the role.
2. **Competency-based:** each question maps to a specific competency.
3. **Evidence-based:** behavioural ("Tell me about a time…") and situational ("How would you handle…") questions.
4. **Spread across a panel:** each competency is owned by a stage, so interviewers cover different ground and their views can be compared.
5. **Scored:** against written anchors, not gut feel.

## What goes in it

- **3–10 competencies** (4–6 is typical). Mark the few that are true
  must-haves; a must-have that is really a nice-to-have shrinks the field for
  no reason and screens out people from non-traditional paths.
- **A one-sentence definition** of what good looks like in this role.
- **An anchor for each score, 1 to 4.** Write them for this role, never generic.
  - 1: no relevant example, or the example shows the opposite.
  - 2: a relevant example with a named gap.
  - 3: a specific example at the level the role needs.
  - 4: as 3, plus an observable step beyond it.
- **An interview plan:** which stage owns which competencies, with 2–4
  questions each and a few follow-up probes (What was the outcome? What was
  your part versus the team's? What would you do differently?).

## Writing a competency so it can be scored

A competency has to be checkable against what the candidate said. Vague
criteria get scored on impressions.

**Bad:** "Strong communicator." In practice it becomes a proxy for polish,
accent or confidence, which track background more than ability.

**Good:** "Explains a finance issue to a non-finance colleague and agrees a
next step that happens." Now there is something to listen for and quote.

**Bad:** "Culture fit." Not a competency. It is where unexamined preference
hides, and it won't survive being read aloud. The script rejects it.

**Good:** whatever the team actually means by it, stated as behaviour:
"Asks for feedback, acts on it, and helps teammates meet shared deadlines."

## Off-limits, explicitly

Do not score, note, comment on, or let influence a score:

| Signal | Why it is out |
| --- | --- |
| Age, graduation years ("class of …"), "digital native", "energy" read as youth | Age discrimination, directly |
| Race, ethnicity, national origin, citizenship, visa or immigration status, name | Protected; a name is a proxy for ethnicity and gender |
| Religion, religious dress or holidays | Protected |
| Sex, gender, gender identity, pronouns, sexual orientation | Protected, and irrelevant to the work |
| Disability, health, medical history | Protected; accommodations are a separate, confidential process |
| Pregnancy, children, childcare, caring for a relative, marriage, family plans | Protected in many places, and a classic unlawful interview question |
| Genetic information, family medical history | Protected (for example under GINA in the US) |
| Military or veteran status, union membership | Protected in many jurisdictions |
| Accent or native language | A proxy for national origin, unless the role genuinely needs a specific language (then assess that language skill directly) |
| Appearance, dress, photo | Irrelevant and a proxy for several protected traits |
| School name or prestige, employer brand | A proxy for money and class (and, for HBCUs and women's colleges, for race and sex). Score the work described, not the logo |
| Current or past salary | Unlawful to ask in many jurisdictions; perpetuates pay gaps |
| Arrests, and convictions unrelated to the role | Heavily regulated; your recruiting lead and counsel handle this |
| "Culture fit", "vibe", "seems like one of us" | Where unexamined preference hides |
| "Strong hire", "make an offer", "move her forward", "Reject.", "lean no", 👍 | The decision is the panel's, made after the debrief. A scorecard holds evidence and scores only; the script never exports raw evaluation prose, and the ATS draft leaves the recommendation field empty for the interviewer |

If the candidate volunteers personal information, leave it out of the extraction and rating process. If an interviewer asked about a sensitive topic, record only the topic and source link as a concern, never the answer. Controlled export labels are not proof that an extraction is accurate or fair.

## Extracting supported work observations

Read the original source against the approved competency. Store a short excerpt only in the temporary input bundle, then choose exact observation codes only if they faithfully represent job-relevant work. The export is a bounded description linked to the source, never a copied quotation. For example:

```
Competency: Reconciliation & accuracy (must-have)
Interviewer rating: 3 — Meets the bar (entered by the interviewer)
Observation 1: Traced the ledger; result: issue identified.
Source: candidate transcript, source row 1, meeting link
```

The interviewer must inspect that source and the rubric anchors before entering a rating, and link the rating to the observation IDs. The agent must not choose the number or promote a suggestion. If the available codes cannot represent the example accurately, leave the observation out and review the original source directly.

**Score the work.** Equivalent work described in different language should be assessed against the same anchors. The script cannot make that judgment.

**When the interview did not cover a competency, use “No evidence — not assessed”.** Missing coverage is a question for the next round, not a low score. Follow-ups use the rubric's planned questions; unrestricted gap prose is withheld.

## The panel debrief

- Each interviewer files their own scores before discussion, so nobody
  anchors on the first opinion voiced. The script hides every score in the
  panel debrief until all of them have filed. An interviewer who filed
  without scoring anything counts once the bundle marks them `filed: true`.
- Spend the debrief on competencies where scores diverged (a spread of 2 or
  more on the 1–4 scale) and on must-haves nobody assessed.
- Keep any disqualifying signal separate from the scores, and check it is
  job-related.
- The question is whether the evidence shows the candidate meets the bar on
  what the job needs, not whether they were likeable.

## What people still decide

This skill produces source-linked work observations and records interviewer-entered ratings. It does not hire anyone.
Interviewers and the panel bring context that isn't in any rubric, and making
the call is their job. The value here is that they spend their judgement on
well-evidenced scorecards instead of reconstructing an interview from memory.
