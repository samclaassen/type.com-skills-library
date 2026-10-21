# Harbor & Vine Brand Voice Guidelines

> DRAFT for the Harbor & Vine account lead. Synthetic example client.

## Generation Metadata
- Client: harbor-and-vine (Harbor & Vine)
- Created: 2026-09-01
- Version: 2
- Replaces: 2026-03-14
- Sources: client-scoped brand book (Drive folder the account lead named), three club newsletters
- Status: approved by the account lead on 2026-09-03
- Overall confidence: High

## Executive Summary

Harbor & Vine sounds like a friend who works at a small coastal winery: warm,
unhurried, specific about place and season. It invites rather than sells.

## We Are / We Are Not

| We Are | We Are Not |
|--------|------------|
| **Warm** — we write to one member at a time | **Gushing** — no hype, no exclamation marks |
| **Unhurried** — short sentences, room to breathe | **Sleepy** — every note still has a point |
| **Specific** — vineyards, harvest dates, pairings | **Snobbish** — no tasting-note jargon for its own sake |
| **Generous** — we share what we know | **Discount-driven** — value is never "cheap" |

## Terminology Guide

| Use | Not | Why |
|-----|-----|-----|
| members | customers | Club language; people join, they don't just buy |
| bottles | products | We sell wine, not SKUs |

Never use: cheap, deal of the year, limited time only.

## Machine-Checkable Rules

```voice-rules
client: harbor-and-vine
updated: 2026-09-01
status: approved
ban: cheap
ban: deal of the year
ban: limited time only
prefer: members | customers
prefer: bottles | products
max_sentence_words: 20
exclamations: none
```
