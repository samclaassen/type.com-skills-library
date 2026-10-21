#!/usr/bin/env python3
"""Month-end close driver.

Reads a close task definition file plus an optional tie-out file and reports:
  * where the close stands against its target close day,
  * the dependency-ordered task sequence,
  * the critical path and what is blocking it,
  * a tie-out log of which balances were verified against which evidence.

Stdlib only, offline, deterministic. Every date comes from the data or from
--period-end / --as-of; the script never reads the wall clock.

Exit codes:
    0  close is on a clean footing (all tie-outs verified and agreeing)
    1  close does not tie: a tie-out difference exceeds tolerance, or a
       balance has no evidence attached
    2  bad input (missing column, unparseable value, unknown dependency,
       dependency cycle, empty file)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

MONEY = Decimal("0.01")
DAYS = Decimal("0.1")
STATUSES = ("not_started", "in_progress", "blocked", "complete")

TASK_REQUIRED = ("task_id", "task_name", "owner", "depends_on", "estimate_days", "status")
TIEOUT_REQUIRED = (
    "account_code",
    "account_name",
    "gl_balance",
    "evidence_balance",
    "evidence_source",
    "evidence_ref",
)


class InputError(Exception):
    """Raised for any malformed input; carries a message naming the field."""


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------


def read_rows(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InputError(f"{path}: file is empty (no header row)")
        header = [name.strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            raise InputError(f"{path}: missing required column(s): {', '.join(missing)}")
        rows: list[dict[str, str]] = []
        for line_no, raw in enumerate(reader, start=2):
            row = {
                (key.strip() if key else key): (value.strip() if isinstance(value, str) else "")
                for key, value in raw.items()
                if key is not None
            }
            if not any(row.get(name) for name in required):
                continue  # skip fully blank spacer lines
            row["__line__"] = str(line_no)
            rows.append(row)
    if not rows:
        raise InputError(f"{path}: no data rows found below the header")
    return rows


def parse_decimal(row: dict[str, str], column: str, path: Path, *, allow_blank: bool = False) -> Decimal | None:
    raw = (row.get(column) or "").strip()
    if raw == "":
        if allow_blank:
            return None
        raise InputError(f"{path} line {row['__line__']}: column '{column}' is empty")
    cleaned = raw.replace(",", "").replace("$", "").replace(" ", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise InputError(
            f"{path} line {row['__line__']}: column '{column}' value {raw!r} is not a number"
        ) from None
    if not value.is_finite():
        raise InputError(f"{path} line {row['__line__']}: column '{column}' value {raw!r} is not finite")
    return -value if negative else value


def parse_date(row: dict[str, str], column: str, path: Path, *, allow_blank: bool = False) -> date | None:
    raw = (row.get(column) or "").strip()
    if raw == "":
        if allow_blank:
            return None
        raise InputError(f"{path} line {row['__line__']}: column '{column}' is empty")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise InputError(
            f"{path} line {row['__line__']}: column '{column}' value {raw!r} is not an ISO date (YYYY-MM-DD)"
        ) from None


def iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InputError(f"{label} value {value!r} is not an ISO date (YYYY-MM-DD)") from None


def money(value: Decimal) -> str:
    """Display form: thousands separators, 2 dp, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):,.2f}"


def plain(value: Decimal) -> str:
    """Machine form: 2 dp, no separators, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):.2f}"


def days(value: Decimal) -> str:
    return f"{value.quantize(DAYS, rounding=ROUND_HALF_UP):.1f}"


# --------------------------------------------------------------------------
# business-day calendar
# --------------------------------------------------------------------------


class Calendar:
    """Business days = Mon-Fri minus an explicit holiday list."""

    def __init__(self, period_end: date, holidays: frozenset[date]) -> None:
        self.period_end = period_end
        self.holidays = holidays

    def is_business_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def index_of(self, day: date) -> int:
        """Business days elapsed after period end, counting `day` itself."""
        if day <= self.period_end:
            return 0
        count = 0
        cursor = self.period_end
        while cursor < day:
            cursor += timedelta(days=1)
            if self.is_business_day(cursor):
                count += 1
        return count

    def date_of(self, index: int) -> date:
        cursor = self.period_end
        remaining = max(index, 0)
        if remaining == 0:
            return self.period_end
        while remaining > 0:
            cursor += timedelta(days=1)
            if self.is_business_day(cursor):
                remaining -= 1
        return cursor


# --------------------------------------------------------------------------
# domain objects
# --------------------------------------------------------------------------


@dataclass
class Task:
    task_id: str
    name: str
    owner: str
    depends_on: list[str]
    estimate: Decimal
    remaining: Decimal
    status: str
    completed_on: date | None
    blocker_note: str
    category: str
    line: str
    finish_index: Decimal = Decimal(0)
    start_index: Decimal = Decimal(0)
    driver: str | None = None
    ready: bool = False
    blocked_by: list[str] = field(default_factory=list)


@dataclass
class TieOut:
    account_code: str
    account_name: str
    gl_balance: Decimal
    evidence_balance: Decimal | None
    evidence_source: str
    evidence_ref: str
    verified_by: str
    verified_on: date | None
    line: str

    @property
    def difference(self) -> Decimal | None:
        if self.evidence_balance is None:
            return None
        return self.gl_balance - self.evidence_balance


def load_tasks(path: Path) -> list[Task]:
    rows = read_rows(path, TASK_REQUIRED)
    tasks: list[Task] = []
    seen: dict[str, str] = {}
    for row in rows:
        task_id = row["task_id"]
        if not task_id:
            raise InputError(f"{path} line {row['__line__']}: column 'task_id' is empty")
        if task_id in seen:
            raise InputError(
                f"{path} line {row['__line__']}: duplicate task_id {task_id!r} (first seen on line {seen[task_id]})"
            )
        seen[task_id] = row["__line__"]

        status = (row["status"] or "").lower()
        if status not in STATUSES:
            raise InputError(
                f"{path} line {row['__line__']}: column 'status' value {row['status']!r} "
                f"is not one of {', '.join(STATUSES)}"
            )

        estimate = parse_decimal(row, "estimate_days", path)
        assert estimate is not None
        if estimate < 0:
            raise InputError(f"{path} line {row['__line__']}: column 'estimate_days' must not be negative")

        remaining = parse_decimal(row, "remaining_days", path, allow_blank=True)
        if remaining is None:
            remaining = Decimal(0) if status == "complete" else estimate
        if remaining < 0:
            raise InputError(f"{path} line {row['__line__']}: column 'remaining_days' must not be negative")
        if status == "complete":
            remaining = Decimal(0)

        completed_on = parse_date(row, "completed_on", path, allow_blank=True)
        if status == "complete" and completed_on is None:
            raise InputError(
                f"{path} line {row['__line__']}: task {task_id!r} is complete but column 'completed_on' is empty"
            )

        depends = [part.strip() for part in (row["depends_on"] or "").replace("|", ";").split(";")]
        depends = [part for part in depends if part]

        tasks.append(
            Task(
                task_id=task_id,
                name=row["task_name"],
                owner=row["owner"] or "unassigned",
                depends_on=depends,
                estimate=estimate,
                remaining=remaining,
                status=status,
                completed_on=completed_on,
                blocker_note=row.get("blocker_note", ""),
                category=row.get("category", ""),
                line=row["__line__"],
            )
        )

    ids = {task.task_id for task in tasks}
    for task in tasks:
        for dep in task.depends_on:
            if dep not in ids:
                raise InputError(
                    f"{path} line {task.line}: task {task.task_id!r} depends on unknown task_id {dep!r}"
                )
            if dep == task.task_id:
                raise InputError(f"{path} line {task.line}: task {task.task_id!r} depends on itself")
    return tasks


def load_tieouts(path: Path) -> list[TieOut]:
    rows = read_rows(path, TIEOUT_REQUIRED)
    tieouts: list[TieOut] = []
    for row in rows:
        if not row["account_code"]:
            raise InputError(f"{path} line {row['__line__']}: column 'account_code' is empty")
        gl_balance = parse_decimal(row, "gl_balance", path)
        assert gl_balance is not None
        tieouts.append(
            TieOut(
                account_code=row["account_code"],
                account_name=row["account_name"],
                gl_balance=gl_balance,
                evidence_balance=parse_decimal(row, "evidence_balance", path, allow_blank=True),
                evidence_source=row["evidence_source"],
                evidence_ref=row["evidence_ref"],
                verified_by=row.get("verified_by", ""),
                verified_on=parse_date(row, "verified_on", path, allow_blank=True),
                line=row["__line__"],
            )
        )
    return tieouts


# --------------------------------------------------------------------------
# scheduling
# --------------------------------------------------------------------------


def topological_order(tasks: list[Task], path: Path) -> list[Task]:
    """Kahn's algorithm, ties broken by input order so output is deterministic."""
    by_id = {task.task_id: task for task in tasks}
    indegree = {task.task_id: len(set(task.depends_on)) for task in tasks}
    dependents: dict[str, list[str]] = {task.task_id: [] for task in tasks}
    for task in tasks:
        for dep in set(task.depends_on):
            dependents[dep].append(task.task_id)

    position = {task.task_id: index for index, task in enumerate(tasks)}
    ready = sorted((task.task_id for task in tasks if indegree[task.task_id] == 0), key=position.get)
    order: list[Task] = []
    while ready:
        current = ready.pop(0)
        order.append(by_id[current])
        for child in dependents[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort(key=position.get)

    if len(order) != len(tasks):
        stuck = sorted(tid for tid, degree in indegree.items() if degree > 0)
        raise InputError(
            f"{path}: dependency cycle detected among task_id(s): {', '.join(stuck)}"
        )
    return order


def schedule(order: list[Task], calendar: Calendar, as_of_index: int) -> None:
    by_id = {task.task_id: task for task in order}
    floor = Decimal(as_of_index)
    for task in order:
        deps = [by_id[dep] for dep in task.depends_on]
        if task.status == "complete":
            assert task.completed_on is not None
            finished = Decimal(calendar.index_of(task.completed_on))
            task.start_index = max(finished - task.estimate, Decimal(0))
            task.finish_index = finished
            task.ready = True
            task.driver = None
            continue

        dep_finish = Decimal(0)
        driver = None
        for dep in deps:
            if dep.finish_index > dep_finish:
                dep_finish = dep.finish_index
                driver = dep.task_id
        if dep_finish >= floor:
            start = dep_finish
        else:
            start = floor
            driver = None
        task.start_index = start
        task.finish_index = start + task.remaining
        task.driver = driver
        task.blocked_by = [dep.task_id for dep in deps if dep.status != "complete"]
        task.ready = not task.blocked_by


def critical_path(order: list[Task]) -> list[Task]:
    if not order:
        return []
    by_id = {task.task_id: task for task in order}
    terminal = min(
        enumerate(order), key=lambda pair: (-pair[1].finish_index, pair[0])
    )[1]
    chain = [terminal]
    cursor = terminal
    guard = 0
    while cursor.driver and guard < len(order):
        cursor = by_id[cursor.driver]
        chain.append(cursor)
        guard += 1
    chain.reverse()
    return chain


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def render_table(headers: list[str], rows: list[list[str]], aligns: str) -> list[str]:
    widths = [len(head) for head in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def fmt(cells: list[str]) -> str:
        parts = []
        for index, cell in enumerate(cells):
            parts.append(cell.rjust(widths[index]) if aligns[index] == "r" else cell.ljust(widths[index]))
        return "  ".join(parts).rstrip()

    lines = [fmt(headers), "  ".join("-" * width for width in widths)]
    lines.extend(fmt(row) for row in rows)
    return lines


STATUS_LABEL = {
    "not_started": "not started",
    "in_progress": "in progress",
    "blocked": "BLOCKED",
    "complete": "complete",
}


def build_report(
    tasks: list[Task],
    order: list[Task],
    tieouts: list[TieOut],
    calendar: Calendar,
    as_of: date,
    as_of_index: int,
    target_days: int,
    tolerance: Decimal,
) -> tuple[list[str], dict, bool]:
    out: list[str] = []
    projected_index = max((task.finish_index for task in order), default=Decimal(0))
    projected_day = int(math.ceil(float(projected_index)))
    projected_date = calendar.date_of(projected_day)
    target_date = calendar.date_of(target_days)
    variance = projected_index - Decimal(target_days)

    total_est = sum((task.estimate for task in tasks), Decimal(0))
    done_est = sum((task.estimate for task in tasks if task.status == "complete"), Decimal(0))
    pct = (done_est / total_est * 100) if total_est > 0 else Decimal(0)
    counts = {status: sum(1 for task in tasks if task.status == status) for status in STATUSES}

    out.append("MONTH-END CLOSE STATUS")
    out.append("=" * 72)
    out.append(f"Period end        : {calendar.period_end.isoformat()}")
    out.append(f"As of             : {as_of.isoformat()} (business day {as_of_index} of the close)")
    out.append(f"Target close      : business day {target_days} ({target_date.isoformat()})")
    out.append(f"Projected close   : business day {days(projected_index)} ({projected_date.isoformat()})")
    sign = "+" if variance > 0 else ""
    verdict = "behind target" if variance > 0 else ("on target" if variance == 0 else "ahead of target")
    out.append(f"Variance to target: {sign}{days(variance)} business days ({verdict})")
    out.append(
        f"Progress          : {days(done_est)} of {days(total_est)} estimated days complete "
        f"({pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%)"
    )
    out.append(
        "Task counts       : "
        + ", ".join(f"{STATUS_LABEL[status]} {counts[status]}" for status in STATUSES)
    )
    out.append("")

    out.append("CRITICAL PATH")
    out.append("-" * 72)
    chain = critical_path(order)
    rows = []
    for task in chain:
        rows.append(
            [
                task.task_id,
                task.name,
                task.owner,
                STATUS_LABEL[task.status],
                days(task.remaining),
                days(task.finish_index),
            ]
        )
    out.extend(
        render_table(
            ["id", "task", "owner", "status", "rem_days", "finish_day"],
            rows,
            "lllrrr",
        )
    )
    open_chain = [task for task in chain if task.status != "complete"]
    if open_chain:
        nxt = open_chain[0]
        reason = nxt.blocker_note or (
            "waiting on " + ", ".join(nxt.blocked_by) if nxt.blocked_by else "ready to start"
        )
        out.append("")
        out.append(f"Next action on the path: {nxt.task_id} {nxt.name} ({nxt.owner}) -- {reason}")
    else:
        out.append("")
        out.append("Next action on the path: none, every task on the critical path is complete")
    out.append("")

    out.append("BLOCKED AND AT-RISK TASKS")
    out.append("-" * 72)
    at_risk = [
        task
        for task in order
        if task.status == "blocked" or (task.status != "complete" and task.blocked_by)
    ]
    if at_risk:
        rows = []
        for task in at_risk:
            rows.append(
                [
                    task.task_id,
                    task.name,
                    task.owner,
                    STATUS_LABEL[task.status],
                    ", ".join(task.blocked_by) or "-",
                    task.blocker_note or "-",
                ]
            )
        out.extend(
            render_table(["id", "task", "owner", "status", "waiting_on", "note"], rows, "llllll")
        )
    else:
        out.append("None. Every incomplete task has its predecessors closed.")
    out.append("")

    out.append("SEQUENCE (dependency order)")
    out.append("-" * 72)
    rows = []
    for position, task in enumerate(order, start=1):
        finish = (
            task.completed_on.isoformat()
            if task.status == "complete" and task.completed_on
            else calendar.date_of(int(math.ceil(float(task.finish_index)))).isoformat()
        )
        rows.append(
            [
                str(position),
                task.task_id,
                task.name,
                task.owner,
                STATUS_LABEL[task.status],
                ", ".join(task.depends_on) or "-",
                days(task.estimate),
                finish,
            ]
        )
    out.extend(
        render_table(
            ["#", "id", "task", "owner", "status", "depends_on", "est_days", "finish"],
            rows,
            "lllllrrl",
        )
    )
    out.append("")

    ties_fail = False
    tie_payload: list[dict] = []
    out.append("TIE-OUT LOG")
    out.append("-" * 72)
    if not tieouts:
        out.append("No tie-out file supplied (--tieouts). Balance verification not assessed.")
    else:
        rows = []
        gl_total = Decimal(0)
        evidence_total = Decimal(0)
        for tie in tieouts:
            gl_total += tie.gl_balance
            difference = tie.difference
            if difference is None:
                verdict = "NO EVIDENCE"
                ties_fail = True
                difference_text = "-"
            else:
                evidence_total += tie.evidence_balance or Decimal(0)
                if abs(difference) <= tolerance:
                    verdict = "tied"
                else:
                    verdict = "DIFFERENCE"
                    ties_fail = True
                difference_text = money(difference)
            rows.append(
                [
                    tie.account_code,
                    tie.account_name,
                    money(tie.gl_balance),
                    money(tie.evidence_balance) if tie.evidence_balance is not None else "-",
                    difference_text,
                    tie.evidence_source or "-",
                    tie.evidence_ref or "-",
                    tie.verified_by or "-",
                    verdict,
                ]
            )
            tie_payload.append(
                {
                    "accountCode": tie.account_code,
                    "accountName": tie.account_name,
                    "glBalance": plain(tie.gl_balance),
                    "evidenceBalance": plain(tie.evidence_balance) if tie.evidence_balance is not None else None,
                    "difference": plain(difference) if difference is not None else None,
                    "evidenceSource": tie.evidence_source,
                    "evidenceRef": tie.evidence_ref,
                    "verifiedBy": tie.verified_by,
                    "verifiedOn": tie.verified_on.isoformat() if tie.verified_on else None,
                    "verdict": verdict,
                }
            )
        out.extend(
            render_table(
                ["account", "name", "gl", "evidence", "diff", "source", "ref", "by", "verdict"],
                rows,
                "llrrrllll",
            )
        )
        out.append("")
        out.append(
            f"Accounts verified : {sum(1 for tie in tieouts if tie.evidence_balance is not None)} "
            f"of {len(tieouts)}"
        )
        out.append(f"GL total          : {money(gl_total)}")
        out.append(f"Evidence total    : {money(evidence_total)} (verified accounts only)")
        out.append(f"Tolerance applied : {money(tolerance)} per account")
    out.append("")

    out.append("VERDICT")
    out.append("-" * 72)
    if tieouts and ties_fail:
        out.append("CLOSE DOES NOT TIE. Resolve the accounts marked DIFFERENCE or NO EVIDENCE")
        out.append("before signing off the close. Exit status 1.")
    elif tieouts:
        out.append("All supplied balances tie to evidence within tolerance. Exit status 0.")
    else:
        out.append("Status reported without balance verification. Exit status 0.")

    payload = {
        "periodEnd": calendar.period_end.isoformat(),
        "asOf": as_of.isoformat(),
        "asOfBusinessDay": as_of_index,
        "targetCloseDay": target_days,
        "targetCloseDate": target_date.isoformat(),
        "projectedCloseDay": days(projected_index),
        "projectedCloseDate": projected_date.isoformat(),
        "varianceDays": days(variance),
        "percentCompleteByEstimate": str(pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
        "taskCounts": counts,
        "criticalPath": [
            {
                "taskId": task.task_id,
                "taskName": task.name,
                "owner": task.owner,
                "status": task.status,
                "remainingDays": days(task.remaining),
                "finishDay": days(task.finish_index),
            }
            for task in chain
        ],
        "blocked": [
            {
                "taskId": task.task_id,
                "taskName": task.name,
                "owner": task.owner,
                "status": task.status,
                "waitingOn": task.blocked_by,
                "note": task.blocker_note,
            }
            for task in at_risk
        ],
        "sequence": [task.task_id for task in order],
        "tieOuts": tie_payload,
        "tieOutsPass": not ties_fail,
    }
    return out, payload, ties_fail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive a month-end close from a task definition file.")
    parser.add_argument("--tasks", required=True, help="close task definition CSV")
    parser.add_argument("--tieouts", help="tie-out CSV (account balance vs supporting evidence)")
    parser.add_argument("--period-end", required=True, help="accounting period end date, YYYY-MM-DD")
    parser.add_argument("--as-of", required=True, help="status date, YYYY-MM-DD (never the wall clock)")
    parser.add_argument("--target-close-days", type=int, default=5, help="target close in business days (default 5)")
    parser.add_argument("--holidays", default="", help="comma-separated non-working YYYY-MM-DD dates")
    parser.add_argument("--tieout-tolerance", default="0.00", help="per-account tie-out tolerance (default 0.00)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of the report")
    args = parser.parse_args(argv)

    try:
        period_end = iso_date(args.period_end, "--period-end")
        as_of = iso_date(args.as_of, "--as-of")
        if as_of < period_end:
            raise InputError("--as-of must not be earlier than --period-end")
        if args.target_close_days < 1:
            raise InputError("--target-close-days must be at least 1")
        try:
            tolerance = Decimal(args.tieout_tolerance)
        except InvalidOperation:
            raise InputError(f"--tieout-tolerance value {args.tieout_tolerance!r} is not a number") from None
        if tolerance < 0:
            raise InputError("--tieout-tolerance must not be negative")

        holidays = frozenset(
            iso_date(part.strip(), "--holidays") for part in args.holidays.split(",") if part.strip()
        )
        calendar = Calendar(period_end, holidays)

        tasks = load_tasks(Path(args.tasks))
        order = topological_order(tasks, Path(args.tasks))
        as_of_index = calendar.index_of(as_of)
        schedule(order, calendar, as_of_index)

        tieouts = load_tieouts(Path(args.tieouts)) if args.tieouts else []

        lines, payload, ties_fail = build_report(
            tasks, order, tieouts, calendar, as_of, as_of_index, args.target_close_days, tolerance
        )
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("\n".join(lines))
    sys.stdout.flush()
    return 1 if ties_fail else 0


if __name__ == "__main__":
    sys.exit(main())
