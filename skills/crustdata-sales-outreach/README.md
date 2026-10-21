# Sales Outreach

Writes the cold email, the call script, the LinkedIn note, the reply, and the whole sequence. Every touch opens on a real, dated signal about the person you're writing to. Powered by [Crustdata](https://crustdata.com)'s real-time data on 200M+ companies and 800M+ people.

It finds the reason to write before it writes.

## What it does

Give it a person's profile URL, an account, or a list. It pulls one specific recent signal about them, then writes around it:

- **Cold email** — under 90 words, one hook, one ask, plus 2-3 angle variants to test
- **Call script** — opener, reason for the call, discovery questions, and written branches for the five objections you'll actually hear, plus a 15-second voicemail
- **LinkedIn touches** — connection note, first DM, follow-up, break-up, and which of their recent posts is worth a genuine comment first
- **Reply handler** — paste what they wrote back; it classifies the reply and drafts the advance
- **Objection talk-tracks** — diagnoses the real objection behind the stated one, then two versions of the answer (direct and soft)
- **Sequence builder** — a day-by-day cadence with a different angle per touch, every touch written out, marked 1:1 or merge-field
- **Signal-triggered drafts** — a raise, a new exec, a champion who just changed jobs: the draft references the actual event, with its date

The signals come from their recent posts, their company's funding and news, and what they're hiring for. **If no real signal exists, it says so and offers a different angle. It never invents one.**

## Example

**Input:**
> "Write a cold email to linkedin.com/in/sarahchen — she's VP Engineering at Acme. We sell incident response tooling."

**Before** — what a generic AI writer produces:

> Hi Sarah, I hope this email finds you well. I wanted to reach out because I think our platform could help streamline your data infrastructure and unlock efficiencies for your engineering team. Would you be open to a quick 15-minute call next week to explore how we might be able to help?

**After** — what this skill produces, after pulling her recent activity and Acme's open roles:

> **Subject:** your on-call post
>
> Sarah, your post last Tuesday about the on-call rotation eating your senior engineers is the same thing I hear from every team that just doubled.
>
> You have four platform roles open right now. The stretch between posting them and having them productive is usually when the pager does the most damage.
>
> We cut alert volume about 60% for a 200-person team in the same spot, mostly by killing duplicate pages.
>
> Worth 15 minutes Thursday?

Same product, same buyer. The difference is the first line is checkable, and it's dated.

Every draft also comes back with the evidence attached, so you can verify it in five seconds:

| Hook | Source | Date |
|---|---|---|
| On-call rotation post | Her recent posts | 6 days ago |
| 4 open platform roles | Acme job postings | Newest added 11 days ago |

## How it works

1. **Finds the hook** — Crustdata's social post, company enrichment, and job posting APIs pull the person's recent activity, their company's funding and news, and the roles they're hiring for. One specific dated signal, not a profile summary
2. **Writes the touch around it** — the hook opens the copy, the bridge connects it to the pain you sell against, and one low-friction ask closes. Your voice comes from your saved persona config, or from one question if you don't have one
3. **Cleans it and hands it off** — every draft is run through a strict no-slop pass (no em dashes, no "I hope this finds you well", nothing that survives a find-and-replace of the company name). For a list, you get a campaign CSV plus a written sequence spec you can paste into whatever sequencer you run

Nothing sends itself. Drafts stay drafts, and outbound leaves as a file you load and start.

## Works well with

- **crustdata-sales-prospecting** — builds the list and fires the signals this skill writes on. A "your champion just moved to a new company" row is the highest-converting input there is
- **crustdata-account-research** — deep-dive the account first when the deal is worth it; its account plan gives you the pain map
- **crustdata-icp-builder** — one LinkedIn URL sets up your voice and what you sell, so drafts sound like you from the first run
- **crustdata-meeting-prep** — when the touch lands and a call gets booked, it briefs you on the same person in one screen
- **crustdata-warm-path-deal-workspace** — when there's a warm route in, take it; a referral opener beats the best cold email you'll write

All optional. With no config saved, the skill asks one question and runs.

## Setup

### type.com

1. Add this skill to a Space from the type.com Skills Library.
2. Ask for it by name (`crustdata-sales-outreach`) or just describe the task. type.com's built-in Crustdata lookups (person profile and verified business email from a LinkedIn URL) work with no setup.
3. For everything else (company data, people search, job listings, posts), connect Crustdata's MCP server (`https://install.crustdata.com/mcp`) with your own Crustdata account when the skill asks.

### Outside type.com

### Claude.ai (web) or Claude Desktop (macOS/Windows)

1. Go to **Customize > Connectors > Add custom connector**
2. Paste `https://install.crustdata.com/mcp` and click **Add** ([step-by-step guide](https://support.anthropic.com/en/articles/11175166-getting-started-with-custom-integrations-using-remote-mcp))
3. Download the [`.skill` file](https://github.com/crustdata/skills/releases) and upload it to your project
4. Ask Claude: "Write a cold email to [paste a LinkedIn URL]" or "They replied saying it's too expensive — what do I say?"

### Claude Code (CLI)

1. Add the Crustdata MCP server: `claude mcp add --transport http crustdata https://install.crustdata.com/mcp`
2. Ask: "write a cold email to this person" (or name the `crustdata-sales-outreach` skill)

## Crustdata tools used

All tools run inside `execute({ code })` scripts via `callTool("<tool>", params)`.

| Tool | What it does | Cost |
|------|-------------|------|
| `company_identify` | Resolves names and domains to company records | Free |
| `social_post_list_live` | Their recent posts — the best hook there is | 1 cr per post |
| `company_enrich` | Funding, news, headcount growth for the account | 2 cr per match |
| `job_search` | What they're hiring for, and which tools they name | ~0.03 cr/result |
| `person_contact_enrich` | Business emails for a list (opt-in, cost quoted first) | Up to 5 cr/person |

A single well-hooked cold email typically costs 3-5 credits. Contact details are never pulled without you approving the cost first.

## Evals

Test cases in [`evals.json`](./evals.json) cover hook-grounded cold email, the no-fabrication path when a person has no recent signal, reply and objection handling, a full multi-touch sequence, and a list run with cost-confirmed enrichment.

See [SKILL.md](./SKILL.md) for the full methodology.
