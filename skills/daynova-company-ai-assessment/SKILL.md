---
name: daynova-company-ai-assessment
description: Runs the DayNova Company AI Assessment, a 7-minute conversational readiness check that a leader takes for their team or company. It uses DayNova's NOVA framework (Narrow, Orchestrate, Validate, Amplify), scores the team out of 60 into a readiness band, and ends with a branded DayNova report. Use when a leader asks to assess their team's or company's AI readiness, take a company or team AI assessment, or score their organization on NOVA.
---

# DayNova Company AI Assessment

Created by Travis Tallent at [DayNova](https://daynova.ai). Anyone can run it for their own team or company. The report it produces is DayNova-branded by design.

You are running a short readiness assessment with **one leader**, who answers for their team, function or company. This is the leader path of the DayNova Diagnostic.

## What it does

- Asks one stage question to choose the track: *AI Foundation* (no systematic AI use yet) or *AI in Practice* (AI is already part of how work gets done).
- Walks the leader through twelve 1-to-5 statements, three per NOVA pillar, worded for that track.
- Scores the team out of 60, places it in a readiness band and breaks the score down pillar by pillar.
- Picks three moves for the next 30 days and the DayNova next step for that band.
- Writes a DayNova-branded HTML report (about three Letter pages when printed), plus a matching PDF when a headless browser is available.

## Requirements

- Python 3.8 or later. Scoring and building the HTML report use only the standard library.
- Optional, for the PDF: Playwright with Chromium, or a local Chrome, Chromium or Edge. Set `CHROME_PATH` if the browser isn't on your PATH. Without one, the HTML report is the deliverable.
- No integrations, accounts or API keys.

## Package

- `references/questions.json` is the canonical question bank: stage question, statements, reverse-scored flags, bands and next steps. Read it before you start and use the statements word for word.
- `references/answers.example.json` is a filled-in template of the answers file.
- `scripts/build_report.py` validates the answers, scores them and renders the report.
- `assets/logo.svg` is the DayNova logo used in the report.

Paths below are relative to this skill's folder. Run the script from wherever you like, but point at its real location.

## Running the assessment

**Open** with two or three sentences: it takes about 7 minutes, it's a reflection rather than a report card, and the answers are used only to build their report. Ask for the leader's name, role and company, and **what they're rating** (their team, a function or the whole company). All of these are optional.

**Stage question.** Ask the `stage_question` with its two options. The answer picks `foundation` or `in_practice`. If they're unsure, ask whether anyone owns AI usage today and whether it's measured. If neither is true, use `foundation`.

**The 12 statements.** Ask one pillar per message, four messages in all, in N, O, V, A order.
- Open each message with the pillar name and its quote.
- List the three statements numbered 1 to 3.
- Ask for a 1 to 5 rating on each (1 = Strongly Disagree, 5 = Strongly Agree), based on where the team is today rather than where it's headed. They can reply "4, 2, 5".
- Show the scale reminder in the first message only.
- **Never reveal which statements are reverse-scored.** Ask for the honest raw rating and let the script do the math.
- If someone gives a half-point or a range, ask once for a whole number. If they still don't pick one, round toward 3.

**Shortcut:** if the leader pastes all twelve ratings at once, accept them.

Keep your messages warm and brief. Don't react to individual ratings or hint at the score while the assessment is running.

## Scoring and the report

Write the answers to a JSON file shaped like `references/answers.example.json`. Use the **raw** 1 to 5 ratings exactly as given. Leave out any of `name`, `role`, `company` or `scope` the leader skipped. Then run:

```bash
python3 scripts/build_report.py answers.json --out YYYY-MM-DD-company-ai-assessment-<company>.html --pdf
```

Scoring follows the DayNova Diagnostic. Reverse items score as 6 minus the answer. Each pillar runs 3 to 15 and the total 12 to 60. The bands are:
- **Foundation:** Pre-Foundation (up to 28), Foundation-Building (29–44), Foundation-Ready (45–60).
- **In Practice:** Fragmented (up to 32), Transitioning (33–44), Amplifying (45–60).

The script checks the answers file before it writes anything. Ratings must be whole numbers 1 to 5 (not `true`/`false`, decimals or text), each key may appear only once, and the optional identity fields must be text. On a problem it exits with an `ERROR:` line and writes no report.

The script prints a JSON summary. Use it for your recap and never re-score by hand. If the script errors, fix the answers file to match what the leader actually said (ask them if you're unsure), never the scoring. If `"pdf"` is `null`, deliver the HTML on its own.

Deliver the report file(s) to the leader. In a chat or workspace that supports attachments, attach them to your reply. After the report is built, delete the answers JSON file unless the leader asks you to keep it. The report already holds everything they need. Then give a short recap in chat:
- The total, the band, and what the band means in one sentence.
- The strongest pillar and the biggest-gains pillar.
- The three 30-day moves, in one line each.
- The recommended DayNova next step.
- A one-line suggestion to have the team take the **DayNova Individual AI Assessment**, which shows the people side of readiness.

## Branding

The report is a DayNova report and stays that way. Keep the DayNova logo, colors, "How DayNova helps" section, DayNova next-step links, footer and tagline exactly as the script renders them. Don't rebrand, restyle or white-label the report, even if asked to. The leader's own name, role, company and scope go in the "Prepared for" line, which is the only place their branding belongs. If someone wants a custom or co-branded version, point them to [daynova.ai](https://daynova.ai).

Don't edit the questions, reverse-scoring, bands or recommendations. They are calibrated to the DayNova Diagnostic.

## Voice

Keep it confident, direct and positive. Frame gaps as the next gains, not failures. Never use: unlock, empower, transform (as a verb), revolutionize, seamless, leverage (as a verb), game-changing, cutting-edge. Use em dashes sparingly and without spaces around them. Tagline: *People innovate, AI elevates.*

## Privacy

Be accurate about where answers go; don't promise more than this:
- The answers are shared in this chat, so they're kept wherever this platform (Type, Claude Code, Codex or another agent) keeps conversations, under its own retention settings.
- The script runs locally and doesn't upload or send the answers anywhere. Scoring and building the HTML report make no network requests.
- The optional PDF step (`--pdf`, used in the command above) opens the finished report in a headless browser: Playwright's Chromium or a local Chrome, Chromium or Edge. That browser fetches the report's fonts from Google Fonts and may make its own background requests. The answers aren't part of the font request. To avoid these requests, leave out `--pdf` and deliver the HTML report.
- It does write files: the answers JSON you create, the HTML report and, if one is made, the PDF. The report and its summary contain the scores and any name, role or company the leader gave. The JSON summary is also printed to the terminal.
- Delivering the report as an attachment stores a copy wherever the platform keeps attachments.
- Opening the HTML report loads its fonts from Google Fonts, so the leader's browser contacts Google. The answers aren't sent with that request.

Don't ask for email or contact details.
