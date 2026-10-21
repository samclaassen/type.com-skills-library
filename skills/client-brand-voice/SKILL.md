---
name: client-brand-voice
description: Build, apply, and track separate brand voice guidelines for each agency client, stored at clients/<slug>/brand-voice.md, and review drafts against one named client's guidelines with line-level suggested edits.
---

# Client Brand Voice

Agencies write for many brands at once. This skill keeps one set of voice
guidelines per client and never lets them leak into another client's copy.

It has three modes:

1. **Build**: find a client's brand materials (only in the sources the user
   scopes to that client) and turn them into draft guidelines at
   `clients/<slug>/brand-voice.md`.
2. **Review**: check a draft against one client's guidelines and return
   line-level suggested edits.
3. **List**: show which clients have guidelines, their status, and when they
   were last updated.

Client profiles follow `CLIENTS.md` in this package. Read it before your first
run. The rules that matter most here:

- **One client per run.** Every operation names a client. If the user doesn't
  name one, ask which client before you do anything. Never load two clients'
  guidelines in the same review, and never apply one client's guidelines to
  another client's copy.
- **Everything client-facing is a draft** for the account lead. Generated
  guidelines and review notes are marked `DRAFT for the <Client> account lead`.
  Nothing goes to the client automatically.
- **Nothing is written without approval.** The user has to approve the
  specific client and file in this conversation first.

## Before you run

This skill ships scripts and sample data alongside this SKILL.md. Before running any command:

1. **Get the files.** Make sure the skill's other files (`scripts/`, `examples/` and anything else listed with this skill) are in your working folder at the same relative paths. Some environments load only SKILL.md; if yours did, fetch each file from this skill's published files and write it to the matching path. In Type, read them with the skill-file tools. Anywhere else, the Type Skills Library API lists every file with its path, content and `sha256`: GET `https://api.type.com/api/public/library/skills` and take the entry with slug `client-brand-voice`.
2. **Check the copies are exact.** Compare each file's size in bytes, not characters (and its hash, where your tools report one), with the published version before running. A copy written out from the published file is fine once its byte size and hash match; never run a script you summarised or reconstructed from memory.
3. **Run from the skill's folder**, calling interpreters explicitly: `python3 scripts/…` and `bash examples/run.sh`.
4. **Try the sample first.** If the skill ships `examples/run.sh` and `examples/expected_output.txt`, run `bash examples/run.sh`; its output should match the expected file exactly. If it doesn't, stop and report the first differing line rather than running on real data.

## Resolve the client first (all modes)

1. Find the `clients/` folder in the user's working folder, not in the agent's
   current directory. Resolve paths from the working folder. If there is no
   working folder or no `clients/` folder, say so and offer to create
   `clients/<slug>/client.json`. Create it only after the user approves.
2. Match the client the user named by folder slug or by `name` in
   `client.json` (for example, "Harbor & Vine" matches `harbor-and-vine`). If
   more than one client matches, or none do, stop and ask. Don't guess.
3. If `client.json` is missing or has no `name`, tell the user exactly which
   file or field is missing and show the minimal JSON from `CLIENTS.md`.
4. Say which client you are working on in your first line, like this:
   `Client: Harbor & Vine (harbor-and-vine)`.

## Mode 1: Build a client's voice guidelines

This combines upstream discovery and guideline generation. It runs for one
client at a time.

### 1. Orient and scope (wait for confirmation)

Explain the steps in a few lines: scope the sources, search them, analyze and
rank what's found, draft guidelines, then save once approved. Next, ask the
user **which sources belong to this client**. Examples: a Drive folder, a
Notion teamspace, a Slack channel, a set of uploaded files, the client's
website (from `client.json`), call recordings with that client.

- Search **only** the sources the user scopes to this client. Don't run
  workspace-wide searches for "brand guidelines". An agency workspace holds
  other clients' brand books, and they would contaminate the result.
- If a connected platform holds several clients' material, confine every
  search to the folder, space, or channel the user named. Put the client's
  name in queries too. Leave out any hit that doesn't clearly belong to
  this client, and list it under "Excluded as possibly another client's".
- If the user scopes no document sources (only chat or call transcripts),
  warn them that confidence will be lower, then continue.
- The agency's own brand materials are not the client's. Leave them out
  unless the user says otherwise.

See `references/discovery-and-ranking.md` for query patterns by platform,
source categories, and the ranking formula.

### 2. Discover, triage, and report

Search the scoped sources. Put each source in one category (AUTHORITATIVE,
OPERATIONAL, CONVERSATIONAL, CONTEXTUAL, or STALE) and rank it. Deep-read the
top sources. Then present a **discovery report** for this client:

- sources found and analyzed, each with its location
- the main brand elements found
- conflicts between sources (for example, a 2023 guide vs. a 2025 rebrand
  deck)
- open questions, each with your recommendation, framed as "confirm or
  override"

Offer four options: generate guidelines now, resolve open questions first,
widen the scope (the user names more sources for this client), or stop.

### 3. Generate the draft guidelines

Follow `references/guideline-template.md`. It includes the We Are / We Are Not
table, voice attributes with evidence, messaging pillars, the tone-by-context
matrix, terminology, confidence scores (`references/confidence-scoring.md`),
open questions, and a source appendix. It also includes a
**Machine-Checkable Rules** block (format in `DATA_CONTRACT.md`) that the
review helper reads.

- Put rules in the `voice-rules` block only when a source states them or the
  user confirms them. Put inferred preferences in the prose sections instead.
- Set `status: draft`. Only the account lead changes it to `approved`.
- Include the privacy rules from upstream: redact names and contact details of
  the client's customers from examples, and flag sensitive material.
- Run the quality check before you present: at least 3 evidenced attributes,
  at least 4 We Are / We Are Not rows, at least 3 tone contexts, a confidence
  score for every section, a source for every extracted element, no PII, and
  a recommendation for every open question.

### 4. Present, then save only with approval

Show the full draft in the conversation. Summarize the confidence breakdown,
the strongest attribute, and how many questions are open. Then ask:
"Save these as the draft guidelines for **<Client>** at
`clients/<slug>/brand-voice.md`?"

Only after the user says yes:

1. If `clients/<slug>/brand-voice.md` already exists, rename it to
   `clients/<slug>/brand-voice-YYYY-MM-DD.md` (today's date) in the same
   folder. Don't overwrite it.
2. Write the new file. Set `updated:` in the rules block to today's date.
3. Run the helper with `--list` to confirm the file parses (see Mode 3). If it
   exits 2, fix the rules block and tell the user what changed.
4. Confirm the full absolute path to the user.

Don't write to any other client's folder. Don't write to the client's own
platforms (Drive, Notion, and so on) unless the user explicitly approves that
specific write.

## Mode 2: Review a draft against a client's guidelines

1. **Load exactly one client's guidelines.** Check in this order:
   `clients/<slug>/brand-voice.md`, then guidelines for this same client
   generated earlier in the conversation. If there are none, offer Mode 1, or
   a generic review (clarity, consistency, legal flags) labelled "not checked
   against <Client> guidelines". Never use a different client's guidelines as
   a fallback.
2. If the guidelines say `status: draft`, tell the user the review is against
   guidelines the account lead hasn't approved yet.
3. **Run the mechanical check** when the guidelines have a `voice-rules`
   block. Save the draft as a UTF-8 text file if it isn't one already, then:

   ```bash
   python3 <skill-dir>/scripts/check_voice_rules.py \
     --clients-dir <working-folder>/clients --client <slug> --draft <draft.txt>
   ```

   Exit 0 means no mechanical violations. Exit 1 means violations were
   listed. Exit 2 means the input is malformed: report the error message and
   don't claim the check passed. Use the script's line numbers.
4. **Do the judgement review** with `references/review-rubric.md`: voice
   attributes, the We Are Not boundaries, tone for this content type,
   terminology the script can't see (capitalization, product names),
   messaging pillars, style guide rules, and legal or compliance flags. The
   legal flags are always checked.
5. **Return review notes marked as a draft:**
   - `DRAFT review for the <Client> account lead`: client, guidelines
     version and date, draft reviewed
   - a summary of what works and the most important fixes
   - a findings table: `Line | Issue | Rule / guideline | Severity | Suggested edit`.
     Every mechanical hit appears here with its line number, alongside your
     judgement findings.
   - before/after rewrites for the 3 to 5 highest-severity issues
   - legal and compliance flags, listed separately
   - open questions from the guidelines that affect this draft
6. Offer to apply the edits, fix only the high-severity issues, or review
   another draft **for the same client**. Don't edit the user's file until
   they approve.

If the user's request conflicts with the guidelines (for example, "add some
exclamation marks" for a client whose policy is `none`), explain the conflict.
Recommend a path and let the user choose: follow the guidelines, adapt for
this case, or override.

## Mode 3: List clients and guideline status

Run:

```bash
python3 <skill-dir>/scripts/check_voice_rules.py --clients-dir <working-folder>/clients --list
```

The output is one line per client folder: the client name, the date the
guidelines were last updated (`updated:` in the rules block, not the file's
modified time), their status (`draft` or `approved`), and rule counts. Clients
without guidelines show as `no brand-voice.md`. Clients whose files don't
parse show as `INVALID`, and the script exits 2. Present this as a table.
Flag guidelines older than 12 months, and any client still at `draft`. This is
a portfolio view, so label every row with its client.

## References

- `references/discovery-and-ranking.md`: scoping sources to one client, query
  patterns, source categories, ranking
- `references/guideline-template.md`: the per-client guidelines template,
  including the rules block
- `references/confidence-scoring.md`: confidence scoring for each section
- `references/review-rubric.md`: the review checklist, the voice-constant /
  tone-flex model, severity levels, and the output format
- `DATA_CONTRACT.md`: file layout, the `voice-rules` format, and the helper's
  output and exit codes
- `CLIENTS.md`: the shared client profile format

## Limits

- The helper only catches mechanical rules: banned phrases, preferred terms,
  sentence length, and exclamation marks. It can't judge voice, tone, claims,
  or context. Clean helper output doesn't mean the copy is on-brand.
- Phrase matching ignores case, respects word boundaries, and stays within
  one line. It doesn't handle stemming or plurals, so `customer` does not
  match `customers` unless both are listed. Sentence splitting is heuristic:
  abbreviations like "e.g." end a sentence early, which can hide an overlong
  sentence.
- Discovery is only as good as the scope the user gives. The skill can't tell
  whether an unlabelled document belongs to this client, so it leaves out
  anything ambiguous and says so.
- Generated guidelines are drafts inferred from the sources. They don't
  replace the client's approved brand book. The account lead approves them
  and sets `status: approved`.
- The skill doesn't send anything to clients, publish anything, or write to
  client accounts or platforms. Any such write needs explicit approval that
  names the client and the change.
- Legal and compliance flags are prompts for human review, not legal advice.
