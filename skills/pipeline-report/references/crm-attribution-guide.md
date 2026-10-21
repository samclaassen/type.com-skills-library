# CRM Attribution Guide

Use this guide when connecting Google Ads performance to HubSpot, Salesforce, or another CRM.

## Source Priority

Follow the customer-approved source hierarchy and attribution policy. The following are evidence options, not a prescribed order or attribution model:

1. native offline conversion imports tied to Google Ads click identifiers
2. CRM records with `gclid`, `gbraid`, or `wbraid`
3. CRM records with complete UTM source, medium, campaign, content, and term fields
4. analytics source reports connected to CRM IDs
5. spreadsheet or BI exports with documented join keys
6. manual notes, only as context

## Matching Rules

Use stable identifiers before fuzzy names:

- click ID
- contact ID
- company ID
- deal ID
- form submission ID
- email address, if allowed
- UTM tuple
- campaign and date, only for aggregate analysis

Do not merge customer or deal records by name alone.

## Lag Handling

Paid-search leads often turn into pipeline after the reporting window closes. When reporting a recent week or month:

- separate same-period pipeline from cohort-matured pipeline
- call out expected lag
- avoid punishing new campaigns before leads have enough time to progress
- use older cohorts when estimating true pipeline quality

## Hygiene Flags

Escalate these before making revenue claims:

- missing click IDs
- inconsistent UTM casing or naming
- campaign names changed in Ads but not CRM
- duplicate contacts or deals
- deals without create date or source
- offline conversion imports older than the expected refresh window
- stage definitions that changed during the reporting window

## Safe Summary Language

Use precise language:

- "CRM-attributed pipeline" when attribution is connected.
- "Observed pipeline from matching UTM fields" when joins are indirect.
- "Lead volume only" when downstream stages are unavailable.
- "Attribution incomplete" when the data cannot support revenue conclusions.

