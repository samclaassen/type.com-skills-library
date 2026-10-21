# Data contract — gl-reconciliation

Two UTF-8 CSV files with a header row, one per side of the reconciliation.
Both files use the **same schema**. Column order does not matter; names must
match exactly (case-sensitive). A UTF-8 BOM is tolerated. Fully blank lines are
skipped; any other malformed row is a hard error naming the file, line, and
column.

## Columns (both files)

| Column | Required | Type | Notes |
| --- | --- | --- | --- |
| `id` | yes | text | Unique within its own file. Printed in every table so a human can find the row again. Duplicates are an error. |
| `date` | yes | `YYYY-MM-DD` | Bank: the date the item posted to the account. GL: the transaction date of the journal line. |
| `amount` | yes | decimal, max 2 dp | Signed. Zero is rejected — drop the row instead. More than 2 decimal places is rejected rather than rounded. |
| `description` | yes (may be empty) | text | Display only. Never used for matching. |
| `reference` | no | text | Check number, payout id, journal id. Display only. |

Thousands separators, a leading `$`, and accounting parentheses are accepted:
`(1,204.55)` parses as `-1204.55`.

## Sign convention (read before exporting)

**Positive increases the cash account; negative decreases it.** Both files must
follow this, from the perspective of the entity being reconciled.

| Item | Ledger A (bank / processor) | Ledger B (GL) |
| --- | --- | --- |
| Customer receipt, deposit, payout in | positive | positive (debit to cash) |
| Payroll, supplier payment, fee, refund | negative | negative (credit to cash) |

Common traps:

- Bank exports that use separate `debit` and `credit` columns must be collapsed
  into one signed `amount` before you run this.
- Some bank exports state the movement from the bank's point of view, which is
  the opposite of yours. If stage 1 matches nothing, check this first.
- GL exports that publish credits as positive need the sign flipped.
- Stripe payout exports give the net payout. If you export gross charges
  instead, the processor fee will show up as an unmatched difference. Pick one
  basis and state it.

## Ledger A — bank statement or processor payouts

Source: bank CSV download or the connected banking integration; or the Stripe
(or comparable) integration's payout report, one row per payout with the net
amount and the arrival date.

## Ledger B — general ledger detail

Source: the account transaction detail / register report for the same account
and the same date range from QuickBooks, Xero, or NetSuite. Not the trial
balance — this needs line-level detail.

Both exports must cover **exactly the same date range**. A different range is
the most common cause of a non-zero residual.

## Control totals

| Argument | Meaning |
| --- | --- |
| `--balance-a` | Closing balance per the bank statement or processor balance report at `--as-of`. |
| `--balance-b` | Closing balance of the GL account at `--as-of`. |

Supply both or neither. When they are omitted, the script substitutes the sum
of each file's movements, which makes the bridge arithmetically self-proving
and therefore much weaker — the report says so explicitly.

The bridge is:

```
control total A
  + items in B not matched to A
  - items in A not matched to B
  - differences inside matched groups
  = control total B
```

A non-zero residual means the two sides did not start from the same place. It
is usually a prior-period break, a mismatched export range, or rows excluded
upstream.

## Rounding

Amounts are parsed and added as `decimal.Decimal` with no precision loss.
Rounding happens only at display time: 2 decimal places, `ROUND_HALF_UP`. The
`--json` output uses the same 2 dp values without thousands separators.
