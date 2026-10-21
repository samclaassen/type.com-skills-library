# Northwind Ledger Brand Voice Guidelines

> DRAFT for the Northwind Ledger account lead. Synthetic example client.

## Generation Metadata
- Client: northwind-ledger (Northwind Ledger)
- Created: 2026-08-18
- Version: 1
- Sources: client-scoped messaging doc, two sales-call transcripts the account lead shared
- Status: draft, awaiting account-lead review
- Overall confidence: Medium

## Executive Summary

Northwind Ledger is precise and plain-spoken. It talks to controllers and
finance leads as peers, backs claims with numbers, and allows a little energy
in launch and event copy.

## We Are / We Are Not

| We Are | We Are Not |
|--------|------------|
| **Precise** — numbers, dates, named features | **Vague** — no "streamline your workflows" |
| **Peer-level** — we talk like a controller would | **Salesy** — no hype words |
| **Calm** — one exclamation mark at most, for real news | **Breathless** — no stacked exclamation marks |
| **Accountable** — claims are qualified | **Absolute** — no guarantees |

## Terminology Guide

| Use | Not | Why |
|-----|-----|-----|
| close | month-end crunch | The client's customers call it "the close" |
| finance teams | users | We write to teams, not seats |

Never use: revolutionary, game-changer, guarantee, seamless.

## Machine-Checkable Rules

```voice-rules
client: northwind-ledger
updated: 2026-08-18
status: draft
ban: revolutionary
ban: game-changer
ban: guarantee
ban: seamless
prefer: finance teams | users
prefer: close | month-end crunch
max_sentence_words: 30
exclamations: max 1
```
