#!/usr/bin/env python3
"""Two-ledger reconciliation with staged matching.

Reconciles ledger A (bank statement, or payment-processor payout report)
against ledger B (the general ledger cash or clearing account) in three passes:

  stage 1  exact      same amount, same date
  stage 2  tolerance  same amount, date within --date-tolerance days
  stage 3  batch      many rows on one side summing to a single row on the
                      other, inside --batch-window days and within
                      --amount-tolerance (aggregated payouts)

Outputs matched pairs and groups, unmatched items on each side, and a
reconciling-items bridge from the ledger A control total to the ledger B
control total. If the bridge does not tie exactly, the script prints the
discrepancy and exits non-zero.

Stdlib only, offline, deterministic: matching order is fixed by (date, amount,
id) and never depends on dict ordering or the clock.

Exit codes:
    0  the bridge ties
    1  the bridge does not tie to the control totals
    2  bad input (missing column, unparseable value, empty file)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

MONEY = Decimal("0.01")
REQUIRED = ("id", "date", "amount", "description")
MAX_DP_STATES = 200_000


class InputError(Exception):
    """Raised for any malformed input; carries a message naming the field."""


# --------------------------------------------------------------------------
# input
# --------------------------------------------------------------------------


@dataclass
class Entry:
    entry_id: str
    entry_date: date
    amount: Decimal
    description: str
    reference: str
    line: str
    side: str
    stage: str = ""
    group: str = ""


@dataclass
class Group:
    group_id: str
    stage: str
    a_items: list[Entry]
    b_items: list[Entry]

    @property
    def a_total(self) -> Decimal:
        return sum((item.amount for item in self.a_items), Decimal(0))

    @property
    def b_total(self) -> Decimal:
        return sum((item.amount for item in self.b_items), Decimal(0))

    @property
    def difference(self) -> Decimal:
        return self.a_total - self.b_total


def money(value: Decimal) -> str:
    """Display form: thousands separators, 2 dp, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):,.2f}"


def plain(value: Decimal) -> str:
    """Machine form: 2 dp, no separators, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):.2f}"


def load_ledger(path: Path, side: str) -> list[Entry]:
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    entries: list[Entry] = []
    seen: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InputError(f"{path}: file is empty (no header row)")
        header = [name.strip() for name in reader.fieldnames]
        missing = [name for name in REQUIRED if name not in header]
        if missing:
            raise InputError(f"{path}: missing required column(s): {', '.join(missing)}")
        for line_no, raw in enumerate(reader, start=2):
            row = {
                (key.strip() if key else key): (value.strip() if isinstance(value, str) else "")
                for key, value in raw.items()
                if key is not None
            }
            if not any(row.get(name) for name in REQUIRED):
                continue
            entry_id = row.get("id") or ""
            if not entry_id:
                raise InputError(f"{path} line {line_no}: column 'id' is empty")
            if entry_id in seen:
                raise InputError(
                    f"{path} line {line_no}: duplicate id {entry_id!r} (first seen on line {seen[entry_id]})"
                )
            seen[entry_id] = str(line_no)

            raw_date = row.get("date") or ""
            try:
                entry_date = date.fromisoformat(raw_date)
            except ValueError:
                raise InputError(
                    f"{path} line {line_no}: column 'date' value {raw_date!r} is not an ISO date (YYYY-MM-DD)"
                ) from None

            amount = parse_amount(row.get("amount") or "", path, line_no)
            entries.append(
                Entry(
                    entry_id=entry_id,
                    entry_date=entry_date,
                    amount=amount,
                    description=row.get("description", ""),
                    reference=row.get("reference", ""),
                    line=str(line_no),
                    side=side,
                )
            )
    if not entries:
        raise InputError(f"{path}: no data rows found below the header")
    entries.sort(key=lambda entry: (entry.entry_date, entry.amount, entry.entry_id))
    return entries


def parse_amount(raw: str, path: Path, line_no: int) -> Decimal:
    if raw == "":
        raise InputError(f"{path} line {line_no}: column 'amount' is empty")
    cleaned = raw.replace(",", "").replace("$", "").replace(" ", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise InputError(f"{path} line {line_no}: column 'amount' value {raw!r} is not a number") from None
    if not value.is_finite():
        raise InputError(f"{path} line {line_no}: column 'amount' value {raw!r} is not finite")
    if value == 0:
        raise InputError(f"{path} line {line_no}: column 'amount' is zero; drop the row instead")
    if -value.as_tuple().exponent > 2:
        raise InputError(
            f"{path} line {line_no}: column 'amount' value {raw!r} has more than 2 decimal places; "
            "round to the minor unit before reconciling"
        )
    return -value if negative else value


def cents(value: Decimal) -> int:
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------
# matching stages
# --------------------------------------------------------------------------


def stage_exact(a_open: list[Entry], b_open: list[Entry], groups: list[Group], counter: list[int]) -> None:
    buckets: dict[tuple[date, Decimal], list[Entry]] = {}
    for entry in b_open:
        buckets.setdefault((entry.entry_date, entry.amount), []).append(entry)
    for entry in a_open:
        if entry.stage:
            continue
        bucket = buckets.get((entry.entry_date, entry.amount))
        while bucket:
            candidate = bucket.pop(0)
            if candidate.stage:
                continue
            counter[0] += 1
            group_id = f"M{counter[0]:03d}"
            entry.stage = candidate.stage = "exact"
            entry.group = candidate.group = group_id
            groups.append(Group(group_id, "exact", [entry], [candidate]))
            break


def stage_tolerance(
    a_open: list[Entry], b_open: list[Entry], groups: list[Group], counter: list[int], tolerance_days: int
) -> None:
    if tolerance_days <= 0:
        return
    for entry in a_open:
        if entry.stage:
            continue
        best: Entry | None = None
        best_key: tuple[int, date, str] | None = None
        for candidate in b_open:
            if candidate.stage or candidate.amount != entry.amount:
                continue
            gap = abs((candidate.entry_date - entry.entry_date).days)
            if gap > tolerance_days:
                continue
            key = (gap, candidate.entry_date, candidate.entry_id)
            if best_key is None or key < best_key:
                best, best_key = candidate, key
        if best is not None:
            counter[0] += 1
            group_id = f"M{counter[0]:03d}"
            entry.stage = best.stage = "date_tolerance"
            entry.group = best.group = group_id
            groups.append(Group(group_id, "date_tolerance", [entry], [best]))


def subset_for_target(
    candidates: list[Entry], target_cents: int, tolerance_cents: int, max_items: int
) -> tuple[list[Entry], int] | None:
    """Smallest-index-first subset summing to target within tolerance.

    Deterministic bounded dynamic programme over integer minor units. Returns
    the chosen entries and the residual (target - subset sum) in minor units,
    or None when nothing in range is reachable.
    """
    reachable: dict[int, tuple[int, ...]] = {0: ()}
    for index, entry in enumerate(candidates):
        value = cents(entry.amount)
        additions: dict[int, tuple[int, ...]] = {}
        for total, picks in reachable.items():
            if len(picks) >= max_items:
                continue
            new_total = total + value
            if new_total in reachable or new_total in additions:
                continue
            additions[new_total] = picks + (index,)
        reachable.update(additions)
        if len(reachable) > MAX_DP_STATES:
            break
    best_total: int | None = None
    best_picks: tuple[int, ...] = ()
    for total in sorted(reachable):
        if total == 0:
            continue
        gap = abs(total - target_cents)
        if gap > tolerance_cents:
            continue
        picks = reachable[total]
        key = (gap, len(picks), picks)
        if best_total is None or key < (abs(best_total - target_cents), len(best_picks), best_picks):
            best_total, best_picks = total, picks
    if best_total is None:
        return None
    return [candidates[index] for index in best_picks], target_cents - best_total


def stage_batch(
    one_side: list[Entry],
    many_side: list[Entry],
    groups: list[Group],
    counter: list[int],
    window_days: int,
    amount_tolerance: Decimal,
    max_items: int,
    direction: str,
) -> None:
    tolerance_cents = cents(amount_tolerance)
    for entry in one_side:
        if entry.stage:
            continue
        candidates = [
            other
            for other in many_side
            if not other.stage
            and abs((other.entry_date - entry.entry_date).days) <= window_days
            and (other.amount > 0) == (entry.amount > 0)
        ]
        if len(candidates) < 2:
            continue
        found = subset_for_target(candidates, cents(entry.amount), tolerance_cents, max_items)
        if found is None:
            continue
        picked, _residual = found
        if len(picked) < 2:
            continue
        counter[0] += 1
        group_id = f"M{counter[0]:03d}"
        entry.stage = "batch"
        entry.group = group_id
        for item in picked:
            item.stage = "batch"
            item.group = group_id
        if direction == "b-to-a":
            groups.append(Group(group_id, "batch", [entry], picked))
        else:
            groups.append(Group(group_id, "batch", picked, [entry]))


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def render_table(headers: list[str], rows: list[list[str]], aligns: str) -> list[str]:
    widths = [len(head) for head in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def fmt(cells: list[str]) -> str:
        parts = [
            cell.rjust(widths[index]) if aligns[index] == "r" else cell.ljust(widths[index])
            for index, cell in enumerate(cells)
        ]
        return "  ".join(parts).rstrip()

    return [fmt(headers), "  ".join("-" * width for width in widths)] + [fmt(row) for row in rows]


def build_report(
    a_entries: list[Entry],
    b_entries: list[Entry],
    groups: list[Group],
    label_a: str,
    label_b: str,
    balance_a: Decimal,
    balance_b: Decimal,
    balances_supplied: bool,
    as_of: date,
    max_rows: int,
) -> tuple[list[str], dict, bool]:
    out: list[str] = []
    total_a = sum((entry.amount for entry in a_entries), Decimal(0))
    total_b = sum((entry.amount for entry in b_entries), Decimal(0))
    unmatched_a = [entry for entry in a_entries if not entry.stage]
    unmatched_b = [entry for entry in b_entries if not entry.stage]
    unmatched_a_total = sum((entry.amount for entry in unmatched_a), Decimal(0))
    unmatched_b_total = sum((entry.amount for entry in unmatched_b), Decimal(0))
    matched_difference = sum((group.difference for group in groups), Decimal(0))

    stage_names = ("exact", "date_tolerance", "batch")
    stage_counts = {name: [0, 0, Decimal(0)] for name in stage_names}
    for group in groups:
        bucket = stage_counts[group.stage]
        bucket[0] += len(group.a_items)
        bucket[1] += len(group.b_items)
        bucket[2] += group.a_total

    out.append("LEDGER RECONCILIATION")
    out.append("=" * 78)
    out.append(f"As of            : {as_of.isoformat()}")
    out.append(f"Ledger A         : {label_a} -- {len(a_entries)} rows, total {money(total_a)}")
    out.append(f"Ledger B         : {label_b} -- {len(b_entries)} rows, total {money(total_b)}")
    out.append(f"Movement gap A-B : {money(total_a - total_b)}")
    out.append("")

    out.append("MATCHING STAGES")
    out.append("-" * 78)
    rows = [
        [
            name,
            str(sum(1 for group in groups if group.stage == name)),
            str(stage_counts[name][0]),
            str(stage_counts[name][1]),
            money(stage_counts[name][2]),
        ]
        for name in stage_names
    ]
    rows.append(
        [
            "unmatched",
            "-",
            str(len(unmatched_a)),
            str(len(unmatched_b)),
            money(unmatched_a_total - unmatched_b_total),
        ]
    )
    out.extend(render_table(["stage", "groups", "a_rows", "b_rows", "a_amount"], rows, "lrrrr"))
    out.append("")

    out.append("MATCHED ITEMS")
    out.append("-" * 78)
    rows = []
    shown = groups if max_rows <= 0 else groups[:max_rows]
    for group in shown:
        rows.append(
            [
                group.group_id,
                group.stage,
                ",".join(item.entry_id for item in group.a_items),
                ",".join(item.entry_id for item in group.b_items),
                money(group.a_total),
                money(group.b_total),
                money(group.difference),
                group.a_items[0].description if group.a_items else "",
            ]
        )
    if rows:
        out.extend(
            render_table(
                ["group", "stage", "a_ids", "b_ids", "a_amount", "b_amount", "diff", "description"],
                rows,
                "lllrrrrl",
            )
        )
        if max_rows > 0 and len(groups) > max_rows:
            out.append(f"... {len(groups) - max_rows} further matched group(s) not shown (--max-rows)")
    else:
        out.append("No items matched.")
    out.append("")

    for label, items in ((label_a, unmatched_a), (label_b, unmatched_b)):
        out.append(f"UNMATCHED -- {label}")
        out.append("-" * 78)
        if items:
            rows = [
                [
                    entry.entry_id,
                    entry.entry_date.isoformat(),
                    money(entry.amount),
                    entry.reference or "-",
                    entry.description,
                ]
                for entry in items
            ]
            out.extend(render_table(["id", "date", "amount", "reference", "description"], rows, "llrll"))
            out.append(
                f"Subtotal: {money(sum((entry.amount for entry in items), Decimal(0)))} across {len(items)} row(s)"
            )
        else:
            out.append("None.")
        out.append("")

    out.append("RECONCILING ITEMS")
    out.append("-" * 78)
    bridge_rows = [
        [f"Control total per {label_a}", money(balance_a)],
        [f"add: in {label_b} not in {label_a} ({len(unmatched_b)} item(s))", money(unmatched_b_total)],
        [f"less: in {label_a} not in {label_b} ({len(unmatched_a)} item(s))", money(-unmatched_a_total)],
        ["less: differences inside matched groups", money(-matched_difference)],
    ]
    computed_b = balance_a + unmatched_b_total - unmatched_a_total - matched_difference
    bridge_rows.append([f"= computed control total per {label_b}", money(computed_b)])
    bridge_rows.append([f"actual control total per {label_b}", money(balance_b)])
    residual = computed_b - balance_b
    bridge_rows.append(["residual (must be 0.00)", money(residual)])
    out.extend(render_table(["item", "amount"], bridge_rows, "lr"))
    out.append("")
    if not balances_supplied:
        out.append(
            "Control totals were derived from the ledger movements themselves "
            "(--balance-a / --balance-b not supplied),"
        )
        out.append("so this bridge proves the matching arithmetic but not the underlying balances.")
        out.append("")

    if matched_difference != 0:
        out.append("Matched groups carrying a difference (inside --amount-tolerance):")
        for group in groups:
            if group.difference != 0:
                out.append(
                    f"  {group.group_id} {group.stage}: A {money(group.a_total)} vs B {money(group.b_total)} "
                    f"= {money(group.difference)}"
                )
        out.append("")

    out.append("VERDICT")
    out.append("-" * 78)
    ties = residual == 0
    if ties:
        out.append(f"RECONCILED. The bridge ties to {money(balance_b)} exactly. Exit status 0.")
    else:
        out.append(f"DOES NOT TIE. Residual of {money(residual)} is unexplained.")
        out.append(
            "Most often this is a prior-period break (opening balances did not agree), "
            "an export covering a different date range, or rows filtered out upstream."
        )
        out.append("Exit status 1.")
    if unmatched_a or unmatched_b:
        out.append(
            f"{len(unmatched_a) + len(unmatched_b)} open reconciling item(s) still require action; "
            "they are listed above."
        )

    payload = {
        "asOf": as_of.isoformat(),
        "ledgerA": {"label": label_a, "rows": len(a_entries), "total": plain(total_a)},
        "ledgerB": {"label": label_b, "rows": len(b_entries), "total": plain(total_b)},
        "stages": {
            name: {
                "groups": sum(1 for group in groups if group.stage == name),
                "aRows": stage_counts[name][0],
                "bRows": stage_counts[name][1],
                "aAmount": plain(stage_counts[name][2]),
            }
            for name in stage_names
        },
        "matched": [
            {
                "group": group.group_id,
                "stage": group.stage,
                "aIds": [item.entry_id for item in group.a_items],
                "bIds": [item.entry_id for item in group.b_items],
                "aAmount": plain(group.a_total),
                "bAmount": plain(group.b_total),
                "difference": plain(group.difference),
            }
            for group in groups
        ],
        "unmatchedA": [
            {
                "id": entry.entry_id,
                "date": entry.entry_date.isoformat(),
                "amount": plain(entry.amount),
                "reference": entry.reference,
                "description": entry.description,
            }
            for entry in unmatched_a
        ],
        "unmatchedB": [
            {
                "id": entry.entry_id,
                "date": entry.entry_date.isoformat(),
                "amount": plain(entry.amount),
                "reference": entry.reference,
                "description": entry.description,
            }
            for entry in unmatched_b
        ],
        "bridge": {
            "controlTotalA": plain(balance_a),
            "inBNotInA": plain(unmatched_b_total),
            "inANotInB": plain(unmatched_a_total),
            "matchedDifferences": plain(matched_difference),
            "computedControlTotalB": plain(computed_b),
            "actualControlTotalB": plain(balance_b),
            "residual": plain(residual),
            "balancesSupplied": balances_supplied,
        },
        "ties": ties,
    }
    return out, payload, ties


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile two ledgers with staged matching.")
    parser.add_argument("--ledger-a", required=True, help="bank statement or processor payout CSV")
    parser.add_argument("--ledger-b", required=True, help="general ledger CSV for the same account")
    parser.add_argument("--as-of", required=True, help="reconciliation date, YYYY-MM-DD")
    parser.add_argument("--label-a", default="Ledger A", help="display name for ledger A")
    parser.add_argument("--label-b", default="Ledger B", help="display name for ledger B")
    parser.add_argument("--balance-a", help="independent control total for ledger A (closing balance)")
    parser.add_argument("--balance-b", help="independent control total for ledger B (closing balance)")
    parser.add_argument("--date-tolerance", type=int, default=3, help="stage 2 date window in days (default 3)")
    parser.add_argument("--batch-window", type=int, default=5, help="stage 3 date window in days (default 5)")
    parser.add_argument(
        "--amount-tolerance", default="0.00", help="stage 3 absolute amount tolerance per group (default 0.00)"
    )
    parser.add_argument(
        "--batch-max-items", type=int, default=25, help="maximum rows in one batch group (default 25)"
    )
    parser.add_argument(
        "--batch-direction",
        choices=("b-to-a", "a-to-b", "both", "off"),
        default="both",
        help="which side aggregates in stage 3 (default both)",
    )
    parser.add_argument("--max-rows", type=int, default=0, help="cap matched rows displayed (0 = all)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the report")
    args = parser.parse_args(argv)

    try:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            raise InputError(f"--as-of value {args.as_of!r} is not an ISO date (YYYY-MM-DD)") from None
        if args.date_tolerance < 0 or args.batch_window < 0:
            raise InputError("--date-tolerance and --batch-window must not be negative")
        if args.batch_max_items < 2:
            raise InputError("--batch-max-items must be at least 2")
        try:
            amount_tolerance = Decimal(args.amount_tolerance)
        except InvalidOperation:
            raise InputError(f"--amount-tolerance value {args.amount_tolerance!r} is not a number") from None
        if amount_tolerance < 0:
            raise InputError("--amount-tolerance must not be negative")

        a_entries = load_ledger(Path(args.ledger_a), "A")
        b_entries = load_ledger(Path(args.ledger_b), "B")

        groups: list[Group] = []
        counter = [0]
        stage_exact(a_entries, b_entries, groups, counter)
        stage_tolerance(a_entries, b_entries, groups, counter, args.date_tolerance)
        if args.batch_direction in ("b-to-a", "both"):
            stage_batch(
                a_entries, b_entries, groups, counter, args.batch_window, amount_tolerance,
                args.batch_max_items, "b-to-a",
            )
        if args.batch_direction in ("a-to-b", "both"):
            stage_batch(
                b_entries, a_entries, groups, counter, args.batch_window, amount_tolerance,
                args.batch_max_items, "a-to-b",
            )

        total_a = sum((entry.amount for entry in a_entries), Decimal(0))
        total_b = sum((entry.amount for entry in b_entries), Decimal(0))
        balances_supplied = args.balance_a is not None and args.balance_b is not None
        if (args.balance_a is None) != (args.balance_b is None):
            raise InputError("--balance-a and --balance-b must be supplied together")
        try:
            balance_a = Decimal(args.balance_a) if args.balance_a is not None else total_a
            balance_b = Decimal(args.balance_b) if args.balance_b is not None else total_b
        except InvalidOperation:
            raise InputError("--balance-a / --balance-b must be numbers") from None

        lines, payload, ties = build_report(
            a_entries, b_entries, groups, args.label_a, args.label_b,
            balance_a, balance_b, balances_supplied, as_of, args.max_rows,
        )
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("\n".join(lines))
    sys.stdout.flush()
    return 0 if ties else 1


if __name__ == "__main__":
    sys.exit(main())
