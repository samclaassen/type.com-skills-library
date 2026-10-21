# Triage playbook

Adapted from `mcc-hack-audit/rules.md` and `references/scan-contract.md`
(fourteenwm/ppc-ai-skills, MIT). The scan identifies; the operator judges.

## Invariants

- **Identification, not clearance.** No manager or user outside your tree,
  allowlist or the client's own domain is ever auto-cleared. Age and
  familiarity are context, not clearance.
- **The allowlist hides, it does not protect.** An allowlisted manager or
  domain drops out of the findings but keeps its access. Allowlisted managers
  with live links are still listed at the end of every report; re-audit them
  on the same cadence as everything else.
- **Read-only.** The skill removes nothing and notifies nobody. Removal is a
  manual Google Ads UI action with explicit approval naming the client and the
  change.
- **Absence is not evidence.** An account with query errors was not audited.

## Triage order

1. **Hostile manager, live link (CRITICAL).** A manager you already convicted
   has access or is requesting it. Incident mode.
2. **Unknown admin granted recently (CRITICAL).** New admin access for an
   address nobody recognizes is the usual first step before a hostile
   manager link: an admin can accept link requests.
3. **Pending link or ADMIN invitation from unknown parties (HIGH).** A live
   request awaiting acceptance. This is the highest-value catch: you are
   reading it before the breach. Find out who initiated it before anyone
   accepts.
4. **Unknown managers with ACTIVE links, newest link ID first (HIGH).** Fraud
   is recent; forgotten former agencies are old.
5. **Probing (MEDIUM).** Refused or cancelled links from a manager that holds
   or requests a live link elsewhere in the book. Several attempts across
   clients is a campaign, not noise. Investigate as one incident.
6. **Unmapped accounts (MEDIUM/LOW).** Give each an owner or unlink it.
7. **History and hygiene (LOW/INFO).** Old refused/cancelled links, admins
   without a passkey, recent grants to known staff.

A suspicious manager link, all signals together = act immediately: high
(recent) link ID + ACTIVE or PENDING + nobody can name the manager + change
activity on the account after the link appeared.

## Verdict for each unknown manager or user

| Verdict | Looks like | Action |
|---|---|---|
| Expected | Former agency, franchisor or parent company, platform partner, client freelancer; a name and history that fit | Record it; add to the allowlist with a dated note if you accept the visibility trade-off |
| Unknown | Nobody can name it, or a recent grant nobody authorized | Escalate now (below) |
| Hostile | Unknown plus changes after access appeared, or matches incident intel | Incident mode: remove in UI (with approval), rotate client admin credentials, add manager ID to `hostile.json` |

## Escalation when nobody can identify it

1. Check activity after the access appeared: Change History in the UI (or a
   change-history skill) on the affected accounts. Bulk edits after the link
   or grant date confirm it.
2. Put a name on it: account > Admin > Access and security. The API cannot
   name managers outside your tree.
3. Ask the client what they authorized. Compromised client-side admin
   credentials are the known entry path; the finding's `inviter` field shows
   who granted a user.
4. If unauthorized: remove or decline in the UI (with approval), have the
   client rotate credentials and turn on passkeys or 2-step verification,
   then add the manager ID to `hostile.json`.

## Cadence

Quarterly baseline; immediately on any client report of unexpected changes;
after onboarding and after offboarding. For churn between runs, write
`--csv-dir` output each time and diff the two newest link CSVs: new rows are
new links. The API records no link-acceptance event, so this diff is the
link-acceptance alarm.

## Sharing hostile manager IDs

Share an ID beyond your own list only when hostility is confirmed (not just
unknown), it is no longer an active threat in your tree, and you accept it
becoming public. There is no central registry.

## False alarms

| Signal | Likely cause | Then |
|---|---|---|
| Many INTERNAL links | Every account links its parent manager | Normal; not reported |
| Scan-error count about equal to cancelled/closed accounts | Expected: those accounts often refuse queries | Healthy |
| Scan errors on ENABLED accounts | Credentials or permission problem | Fix access; those accounts were not audited |
| `RESOURCE_EXHAUSTED` | Too many parallel workers | Re-run with `--workers 5` |
| Your root MCC shows an unknown manager | Someone manages your tree (parent org or franchisor?) | Expected only if you have one; otherwise investigate |
| Known partner shows as unknown | By design; nothing is auto-trusted | Verify, then allowlist with a note |
| Hostile ID appears after you revoked it | Link history keeps CANCELED rows | MEDIUM history finding; live access only if status is ACTIVE/PENDING |
| Client staff flagged as unknown | Their email domain differs from `website` in client.json | Add the domain to the allowlist or fix `website` |
