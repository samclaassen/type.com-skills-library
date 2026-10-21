# NOTICE

Type (type.com) wrote `sales-to-cs-handoff`: the workflow, the Granola access rules, the data
contract, `build_handoff.py`, the sample data and the tests. Two small parts are adapted from
the MIT-licensed sources below. The full license texts are in `LICENSE.txt`.

## Sources

| Source | Repository @ commit | Author | License |
|---|---|---|---|
| `skills/sales-to-cs-handoff/references/handoff-packet-fields.md` (the packet categories and field labels, including the commitments field) | [mbfinotti/revops-skills](https://github.com/mbfinotti/revops-skills) @ `2dedb3934ca74a1572d70e2a3ce8b96d2cbde1ca` (upstream `HEAD` on 2026-09-14) | Maya-Beth Finotti | MIT, Copyright (c) 2026 Nativa Labs (repository `LICENSE`; the skill's frontmatter also declares `license: MIT`) |
| `skills/14-sales-handoff/SKILL.md`, section "Implementation Stakeholder Roles" | [rvanshur/vertical-gtm-skills](https://github.com/rvanshur/vertical-gtm-skills) @ `835ab5b083ffe60bb98d6d7aeb604387a60abf25` (upstream `HEAD` on 2026-09-29) | Ryan Vanshur | MIT, Copyright (c) 2026 Ryan Vanshur (repository `LICENSE`; the skill's frontmatter also declares `license: MIT`) |

## What was adapted, and the changes made (statement of changes)

From `mbfinotti/revops-skills`:
- The six-category handoff packet inventory (commercial and contract; goals and success
  definition; stakeholders; sales-cycle context; technical and delivery requirements;
  engagement history) became the 12-item `checklist` in `handoff.json`. The script ticks each
  item from what the bundle contains. Some labels keep upstream phrases, for example "Timeline
  expectations set during the sale", "Concerns and objections raised" and "Competitive
  evaluation".
- Upstream's commitments field in `references/handoff-packet-fields.md` ("Commitments and
  promises made: SLAs, training, custom work, roadmap items, support expectations") became the promise categories in `DATA_CONTRACT.md` and `handoff.json`
  (`custom_work`, `sla`, `support`, `training`, `roadmap`, plus the categories added below).
- Changes: upstream is a process-design skill for RevOps (gates, SLAs, packet fields with
  supplier and consumer, segment variants). That design material was not carried over.
  Supplier/consumer columns, must-block versus optional, segment right-sizing, and the
  practitioner citations are dropped. The categories now describe what one account's handoff
  must contain, filled from meeting notes, the CRM and email. Added categories: `feature`,
  `integration`, `pricing`, `discount`, `timeline`, `security`, `other`. Added the champion,
  economic buyer and technical owner checklist items, and the "Meeting notes linked" item in
  place of upstream's "Key call recordings and demo recordings, linked".

From `rvanshur/vertical-gtm-skills`:
- The implementation stakeholder roles (Executive Sponsor, Implementation Champion, IT Lead,
  Power Users, Data Owner) became the `executive_sponsor`, `day_to_day`, `technical_owner`,
  `user` and `data_owner` roles.
- Changes: renamed to snake_case codes and merged with sales roles written by Type
  (`champion`, `economic_buyer`, `signer`, `procurement`, `legal_security`, `influencer`,
  `detractor`, `unknown`). Role definitions in SKILL.md are rewritten around evidence from
  meetings. The upstream readiness scoring (six weighted dimensions, composite score, 3-date
  plan) is not used.

No other upstream text, code or structure is included.

## Repositories checked and not used

Searched on 2026-10-08 for an existing open-source sales-to-CS, onboarding or account handoff
skill built on meeting notes:

- [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins) @ `ae1513ea94dcb74a7f1505ddcf3b0ec3fab327f1` (Apache-2.0). The `sales` and `customer-support` plugins have no sales-to-CS handoff skill. `sales/skills/stakeholder-map` is related, but nothing was taken from it.
- [anthropics/skills](https://github.com/anthropics/skills) @ `683bc88e56f3e09ba94f7055977f3d3aa499f202`: no sales or CS handoff skill.
- [temiayodele66/Enterpret-CSM-Sales-Handoff-Claude-Skill](https://github.com/temiayodele66/Enterpret-CSM-Sales-Handoff-Claude-Skill) @ `7b655e8e63b802e7cd532026411cb20c15fb3e7a`: the closest match (a handoff doc from call transcripts). It has no license ("License: TBD"), so nothing was copied or adapted from it.
- [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills) @ `19392f7a08264ed00486a251f5b2098321771f94`, [coreyhaines31/marketingskills](https://github.com/coreyhaines31/marketingskills) @ `b9ba399dd88b082b926e261e8ccfb843d20aa066`, [wshobson/agents](https://github.com/wshobson/agents) @ `46891e7e60da0e52baf1050b7b6391b64e84c6d9`, [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills) @ `be2a406907dbc61b73e6827ded415c96139d13a2`, [LiChang1125/B2B_Sales_Agent_Skill](https://github.com/LiChang1125/B2B_Sales_Agent_Skill) @ `02b679f77a9da7b44dc8e81584f114f21ff38dd2`, [zarif3624/gtm-skills](https://github.com/zarif3624/gtm-skills) @ `920c5ac06ac58b49d40b81eede960b2cb5635c7f`: no handoff skill built on meeting notes.
- Granola: no public skills repository (the `granola-ai` GitHub organisation has no skill or prompt repos). Granola's MCP documentation was used for facts about tools, plans and access scopes only.

## Packaging

- The two upstream `LICENSE` files are reproduced, unchanged, in `LICENSE.txt`.
