---
name: bfcm-skills
description: A collection of skills for getting a Shopify brand through Black Friday and Cyber Monday - tracking, budgets, offers, email deliverability, stock, shipping cutoffs, support, peak-week status drafts and a post-season cohort review - using each business's own data and approvals.
---

# BFCM Skills

Use these skills together to prepare for, run and review Black Friday and Cyber Monday
(Thanksgiving Nov 26, Black Friday Nov 27 and Cyber Monday Nov 30, 2026).
Each skill works on its own. Run this collection in analysis and draft mode: find problems
and recommend fixes. Obtain approval for specific store, ad account, email platform or
helpdesk changes and for scheduled Slack delivery before enabling them.

## Included skills

### Get ready (October to mid-November)

1. [BFCM Tracking & Attribution Preflight](https://type.com/library/skills/bfcm-tracking-preflight):
   check Meta, Google Ads and TikTok purchase tracking and UTMs against Shopify, and see how far
   platform-reported sales exceeded Shopify last BFCM (a reporting gap, not proof of error).
2. [Ecommerce Bounce Guard](https://type.com/library/skills/ecommerce-bounce-guard):
   catch bot checkouts that are bouncing your Klaviyo flows before they damage deliverability.
3. [BFCM Holiday Budget Planner](https://type.com/library/skills/bfcm-budget-planner):
   plan a daily, per-channel ad budget from MER and contribution margin, with per-day pull-back and scale lines.
4. [Promo Margin Guard](https://type.com/library/skills/promo-margin-guard):
   check what your discount depth really earns after COGS, shipping, fees and returns.
5. [BFCM Offer & Discount Config QA](https://type.com/library/skills/bfcm-offer-config-qa):
   audit live discounts against the offer plan - stacking, timezones, exclusions, code limits - and
   get test checkouts to run before launch.
6. [Product Feed Auditor](https://type.com/library/skills/product-feed-auditor):
   check the Shopping feed's prices and sale prices before the sale goes live.
7. [Inventory Stockout Risk](https://type.com/library/skills/inventory-stockout-risk):
   find the SKUs that will sell out first and what to reorder.
8. [Holiday Shipping Cutoff Checker](https://type.com/library/skills/holiday-shipping-cutoffs):
   work out the last safe order date per shipping service, model the BFCM backlog, and check every
   delivery promise.
9. [Holiday Support Macros](https://type.com/library/skills/holiday-support-macros):
   forecast holiday ticket volume, find policy gaps and stale macros, and draft macros from your
   written policies.
10. [Meta Creative Fatigue](https://type.com/library/skills/meta-creative-fatigue):
    investigate creative decay and prepare refresh briefs before the peak, not during it.
11. [Shopify AEO Checker](https://type.com/library/skills/shopify-aeo-checker):
    see how often AI shopping agents surface your products versus competitors.

### Run it (Thanksgiving to Cyber Monday)

12. [BFCM Live Pulse](https://type.com/library/skills/bfcm-live-pulse):
    a Slack-ready status draft you generate from current exports at each check time - sales against plan,
    MER against the plan's daily lines, and anything breaking (scheduling and delivery need separate setup).
13. [Daily Revenue Check](https://type.com/library/skills/daily-revenue-check):
    one checked net-sales number each morning.
14. [Meta Budget Scaling](https://type.com/library/skills/meta-budget-scaling):
    prepare Meta budget scenarios within your approved economics, caps and change permissions.
15. [Budget Pacer](https://type.com/library/skills/budget-pacer):
    check monthly pacing against committed budgets (it projects from a trailing run rate, so treat
    cyber-week spikes with care).

### Review it (December to January)

16. [BFCM Cohort Quality](https://type.com/library/skills/bfcm-cohort-quality):
    find out whether the customers you bought during BFCM were worth it, and what to change next year.

**Prerequisites:** Meta Budget Scaling (14) and Meta Creative Fatigue (10) require
[Paid Media Operating Definitions](https://type.com/library/skills/paid-media-operating-definitions),
which requires [Marketing Measurement](https://type.com/library/skills/marketing-measurement) and
[Revenue Definitions](https://type.com/library/skills/revenue-definitions).
Daily Revenue Check (13) uses [Revenue Reporting](https://type.com/library/skills/revenue-reporting) and
[Revenue Reconciliation](https://type.com/library/skills/revenue-reconciliation), which both require
[Revenue Definitions](https://type.com/library/skills/revenue-definitions). Install those and agree the
definitions before running these skills. The seven new packages need no other skills.

## A BFCM timeline

| By | Do this | Skill |
| --- | --- | --- |
| Oct 31 | First tracking preflight and bounce check; set the budget plan and daily guardrails from MER, not platform ROAS | Tracking Preflight, Bounce Guard, Budget Planner, Promo Margin Guard |
| Nov 10 | First offer QA; shipping cutoffs and backlog; feed and stock checks; tracking fixed so you have two weeks of clean data | Offer Config QA, Shipping Cutoff Checker, Product Feed Auditor, Inventory Stockout Risk |
| Nov 13 | Support ticket forecast and macro drafts (needs the planner JSON and the cutoffs JSON) | Support Macros |
| Nov 17 | Support macro review; final offer QA and test checkouts; tracking re-check | Support Macros, Offer Config QA, Tracking Preflight |
| Nov 20 | Change freeze starts; approved macros go live; shipping promises corrected | Support Macros, Shipping Cutoff Checker |
| Nov 26 – Dec 1 | Live Pulse status drafts at each check time; daily revenue check each morning | Live Pulse, Daily Revenue Check |
| Mid-December | Switch delivery messaging on each service's last safe order date | Shipping Cutoff Checker |
| January onward | Review the BFCM cohort and plan next year | Cohort Quality |

## How the files hand off

| From | To | File | Notes |
| --- | --- | --- | --- |
| Budget Planner | Live Pulse | plan CSV (`--csv`) | Used unchanged |
| Budget Planner | Shipping Cutoff Checker | plan CSV + `--aov <planner AOV>` | Without `--aov` the checker exits 2 |
| Budget Planner | Support Macros | plan JSON (`--json`) | Pass **last season's** orders only; remove this season's dates |
| Shipping Cutoff Checker | Support Macros | cutoffs JSON (`--json`) | Validated on import; unknown or missing fields stop the run |
| Budget Planner | Cohort Quality | `economics.json` | Optional; reuses the margin inputs |

## Set up for your business

Start from your own numbers and rules. The seven new packages explain their required exports
and include a shared fictional store, Fernhill Goods, so you can preview their outputs.
Existing library skills retain their own setup requirements and examples. Agree on one
revenue definition - net sales = gross sales minus discounts, before returns and refunds, excluding
tax and shipping - and one margin model, and reuse them across the budget plan, the live pulse and
the cohort review. Export numbers with `.` as the decimal separator; the new packages stop with an
input error on comma decimals, `nan` or `inf` rather than guessing. Keep platform-claimed revenue
separate from store sales: never add Meta, Google and TikTok's claimed revenue together and call it
total sales.
