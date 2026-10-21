#!/usr/bin/env python3
"""Actual vs budget by department and account, with drafted commentary.

Applies materiality thresholds (absolute and percentage), classifies each
material variance as volume, timing, or permanent *only where the data
supports it*, and drafts a reviewable sentence for each material line.
Immaterial lines are aggregated, never dropped.

Stdlib only, offline, deterministic.

Exit codes:
    0  the variance bridge ties
    1  it does not tie (line variances do not sum to the operating variance,
       or --control-operating-variance disagrees)
    2  bad input (missing column, unparseable value, unknown account type)
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

MONEY = Decimal("0.01")
PCT = Decimal("0.1")
REQUIRED = ("department", "account_code", "account_name", "account_type", "actual", "budget")
ACCOUNT_TYPES = ("revenue", "cogs", "opex")
CLASSIFICATIONS = ("volume", "timing", "permanent", "unclassified")
MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


class InputError(Exception):
    """Raised for any malformed input; carries a message naming the field."""


@dataclass
class Line:
    department: str
    account_code: str
    account_name: str
    account_type: str
    actual: Decimal
    budget: Decimal
    ytd_actual: Decimal | None
    ytd_budget: Decimal | None
    prior_actual: Decimal | None
    prior_budget: Decimal | None
    actual_units: Decimal | None
    budget_units: Decimal | None
    unit_name: str
    owner: str
    stated_classification: str
    note: str
    line: str

    @property
    def variance(self) -> Decimal:
        return self.actual - self.budget

    @property
    def ytd_variance(self) -> Decimal | None:
        if self.ytd_actual is None or self.ytd_budget is None:
            return None
        return self.ytd_actual - self.ytd_budget

    @property
    def prior_variance(self) -> Decimal | None:
        if self.prior_actual is None or self.prior_budget is None:
            return None
        return self.prior_actual - self.prior_budget

    @property
    def favourable(self) -> bool:
        return self.variance > 0 if self.account_type == "revenue" else self.variance < 0

    @property
    def contribution(self) -> Decimal:
        """Effect of this line's variance on operating income."""
        return self.variance if self.account_type == "revenue" else -self.variance

    @property
    def variance_pct(self) -> Decimal | None:
        if self.budget == 0:
            return None
        return self.variance / abs(self.budget) * 100


def money(value: Decimal) -> str:
    """Display form: thousands separators, 2 dp, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):,.2f}"


def plain(value: Decimal) -> str:
    """Machine form: 2 dp, no separators, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):.2f}"


def pct(value: Decimal | None) -> str:
    if value is None:
        return "n/m"
    return f"{value.quantize(PCT, rounding=ROUND_HALF_UP)}%"


def units(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        return f"{int(quantized):,}"
    return f"{quantized:,.2f}"


def parse_optional_decimal(row: dict[str, str], column: str, path: Path) -> Decimal | None:
    raw = (row.get(column) or "").strip()
    if raw == "":
        return None
    cleaned = raw.replace(",", "").replace("$", "")
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


def load_lines(path: Path) -> list[Line]:
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    lines: list[Line] = []
    seen: dict[tuple[str, str], str] = {}
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
            row["__line__"] = str(line_no)

            for column in ("department", "account_code", "account_name"):
                if not row.get(column):
                    raise InputError(f"{path} line {line_no}: column '{column}' is empty")

            account_type = (row.get("account_type") or "").lower()
            if account_type not in ACCOUNT_TYPES:
                raise InputError(
                    f"{path} line {line_no}: column 'account_type' value {row.get('account_type')!r} "
                    f"is not one of {', '.join(ACCOUNT_TYPES)}"
                )

            key = (row["department"], row["account_code"])
            if key in seen:
                raise InputError(
                    f"{path} line {line_no}: duplicate department/account_code "
                    f"{row['department']}/{row['account_code']} (first seen on line {seen[key]}); "
                    "aggregate it upstream instead"
                )
            seen[key] = str(line_no)

            actual = parse_optional_decimal(row, "actual", path)
            budget = parse_optional_decimal(row, "budget", path)
            if actual is None:
                raise InputError(f"{path} line {line_no}: column 'actual' is empty")
            if budget is None:
                raise InputError(f"{path} line {line_no}: column 'budget' is empty")

            stated = (row.get("classification") or "").lower()
            if stated and stated not in CLASSIFICATIONS:
                raise InputError(
                    f"{path} line {line_no}: column 'classification' value {row.get('classification')!r} "
                    f"is not one of {', '.join(CLASSIFICATIONS)}"
                )

            actual_units = parse_optional_decimal(row, "actual_units", path)
            budget_units = parse_optional_decimal(row, "budget_units", path)

            lines.append(
                Line(
                    department=row["department"],
                    account_code=row["account_code"],
                    account_name=row["account_name"],
                    account_type=account_type,
                    actual=actual,
                    budget=budget,
                    ytd_actual=parse_optional_decimal(row, "ytd_actual", path),
                    ytd_budget=parse_optional_decimal(row, "ytd_budget", path),
                    prior_actual=parse_optional_decimal(row, "prior_actual", path),
                    prior_budget=parse_optional_decimal(row, "prior_budget", path),
                    actual_units=actual_units,
                    budget_units=budget_units,
                    unit_name=row.get("unit_name", "") or "units",
                    owner=row.get("owner", ""),
                    stated_classification=stated,
                    note=row.get("note", ""),
                    line=str(line_no),
                )
            )
    if not lines:
        raise InputError(f"{path}: no data rows found below the header")
    lines.sort(key=lambda item: (item.department, item.account_code))
    return lines


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------


@dataclass
class Classification:
    label: str
    source: str
    evidence: str
    volume_effect: Decimal | None = None
    rate_effect: Decimal | None = None


def classify(line: Line, volume_share: Decimal, timing_share: Decimal) -> Classification:
    if line.stated_classification:
        return Classification(
            line.stated_classification,
            "stated in the file",
            line.note or "classification supplied by the preparer",
        )

    variance = line.variance
    if variance == 0:
        return Classification("unclassified", "no variance", "actual equals budget")

    if (
        line.actual_units is not None
        and line.budget_units is not None
        and line.budget_units != 0
        and line.actual_units != 0
    ):
        budget_rate = line.budget / line.budget_units
        volume_effect = (line.actual_units - line.budget_units) * budget_rate
        rate_effect = variance - volume_effect  # residual, so the two always sum to the variance
        unit_pct = (line.actual_units - line.budget_units) / abs(line.budget_units) * 100
        if abs(volume_effect) >= abs(variance) * volume_share:
            return Classification(
                "volume",
                "unit data",
                (
                    f"{line.unit_name} {units(line.actual_units)} vs {units(line.budget_units)} plan "
                    f"({pct(unit_pct)}); at plan rate that is {money(volume_effect)} of the variance, "
                    f"leaving {money(rate_effect)} of rate/mix"
                ),
                volume_effect,
                rate_effect,
            )

    ytd_variance = line.ytd_variance
    if ytd_variance is not None:
        same_direction = (ytd_variance > 0) == (variance > 0)
        if not same_direction or abs(ytd_variance) < abs(variance) * timing_share:
            return Classification(
                "timing",
                "year-to-date data",
                (
                    f"period variance {money(variance)} but year to date only {money(ytd_variance)} "
                    "-- the swing is not carrying through, so it reads as phasing"
                ),
            )
        prior = line.prior_variance
        if abs(ytd_variance) >= abs(variance):
            persistence = (
                f"; prior period was {money(prior)} the same way"
                if prior is not None and prior != 0 and (prior > 0) == (variance > 0)
                else ""
            )
            return Classification(
                "permanent",
                "year-to-date data",
                (
                    f"year-to-date variance {money(ytd_variance)} is at least as large and in the same "
                    f"direction as the period variance {money(variance)}{persistence}"
                ),
            )
        # Same direction, at least --timing-share but less than all of the period variance:
        # part of the swing is carrying through and part is not. The year-to-date data is
        # present and does not settle it, so the prior period is not allowed to decide it.
        carried = abs(ytd_variance) / abs(variance) * 100
        if prior is None:
            prior_clause = "; no prior-period data to add"
        elif prior != 0 and (prior > 0) == (variance > 0):
            prior_clause = f"; prior period was {money(prior)} the same way, which does not settle it"
        else:
            prior_clause = f"; prior period was {money(prior)}, not the same way"
        return Classification(
            "unclassified",
            "year-to-date data",
            (
                f"year-to-date variance {money(ytd_variance)} runs the same way as the period variance "
                f"{money(variance)} but carries through only {pct(carried)} of it -- above the "
                f"{pct(timing_share * 100)} timing threshold and below a full carry-through, so it may be "
                f"part phasing, part run rate{prior_clause}"
            ),
        )

    prior = line.prior_variance
    if prior is not None and prior != 0 and (prior > 0) == (variance > 0):
        return Classification(
            "permanent",
            "prior period data",
            (
                f"prior period variance {money(prior)} ran the same way as this period's "
                f"{money(variance)}, with no year-to-date data to contradict it"
            ),
        )

    if prior is not None:
        return Classification(
            "unclassified",
            "insufficient data",
            (
                f"no year-to-date data, and the prior period variance {money(prior)} did not run the "
                "same way, so nothing separates timing from run rate"
            ),
        )
    return Classification(
        "unclassified",
        "insufficient data",
        "no year-to-date or prior-period data on this row to separate timing from run rate",
    )


def draft_commentary(line: Line, classification: Classification, period_label: str) -> str:
    direction = "favourable" if line.favourable else "unfavourable"
    head = (
        f"{line.account_name} ({line.department}) came in at {money(line.actual)} against a budget of "
        f"{money(line.budget)} for {period_label}, {direction} by {money(abs(line.variance))} "
        f"({pct(abs(line.variance_pct) if line.variance_pct is not None else None)} of budget)."
    )
    owner_clause = f" {line.owner} to confirm before the pack is issued." if line.owner else ""

    if classification.label == "volume":
        body = (
            f" The movement is volume-driven: {classification.evidence}. Hold the plan rate and flex the "
            "forecast with volume rather than repricing the line."
        )
    elif classification.label == "timing":
        body = (
            f" This reads as timing: {classification.evidence}. No change to the full-year expectation is "
            "implied; confirm the phasing with the budget owner."
        )
    elif classification.label == "permanent":
        body = (
            f" This looks like a permanent change in run rate: {classification.evidence}. Reforecast the "
            "remaining periods at the new level."
        )
    else:
        body = (
            f" Cause not determined from the data supplied ({classification.evidence}). Do not assert timing "
            "or run rate in the pack until it is confirmed."
        )
    if line.note and classification.source != "stated in the file":
        body += f" Preparer note: {line.note}."
    return head + body + owner_clause


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
    lines: list[Line],
    period_label: str,
    as_of: date,
    materiality_abs: Decimal,
    materiality_pct: Decimal,
    rule: str,
    volume_share: Decimal,
    timing_share: Decimal,
    control: Decimal | None,
) -> tuple[list[str], dict, bool]:
    out: list[str] = []

    totals = {
        kind: (
            sum((line.actual for line in lines if line.account_type == kind), Decimal(0)),
            sum((line.budget for line in lines if line.account_type == kind), Decimal(0)),
        )
        for kind in ACCOUNT_TYPES
    }
    actual_oi = totals["revenue"][0] - totals["cogs"][0] - totals["opex"][0]
    budget_oi = totals["revenue"][1] - totals["cogs"][1] - totals["opex"][1]
    operating_variance = actual_oi - budget_oi
    contribution_sum = sum((line.contribution for line in lines), Decimal(0))
    residual = contribution_sum - operating_variance
    control_residual = (operating_variance - control) if control is not None else Decimal(0)

    out.append("BUDGET VARIANCE AND COMMENTARY")
    out.append("=" * 78)
    out.append(f"Period            : {period_label} (as of {as_of.isoformat()})")
    out.append(f"Lines read        : {len(lines)} across {len({line.department for line in lines})} department(s)")
    threshold_join = "and" if rule == "and" else "or"
    out.append(
        f"Materiality       : |variance| >= {money(materiality_abs)} {threshold_join} "
        f"|variance %| >= {pct(materiality_pct)}"
    )
    out.append("")

    out.append("P&L SUMMARY")
    out.append("-" * 78)
    rows = []
    for kind, label in (("revenue", "Revenue"), ("cogs", "Cost of revenue"), ("opex", "Operating expenses")):
        actual, budget = totals[kind]
        variance = actual - budget
        line_pct = (variance / abs(budget) * 100) if budget != 0 else None
        rows.append([label, money(actual), money(budget), money(variance), pct(line_pct)])
    oi_pct = (operating_variance / abs(budget_oi) * 100) if budget_oi != 0 else None
    rows.append(["Operating income", money(actual_oi), money(budget_oi), money(operating_variance), pct(oi_pct)])
    out.extend(render_table(["", "actual", "budget", "variance", "var %"], rows, "lrrrr"))
    out.append("")

    out.append("BY DEPARTMENT")
    out.append("-" * 78)
    rows = []
    for department in sorted({line.department for line in lines}):
        dept_lines = [line for line in lines if line.department == department]
        actual = sum((line.actual for line in dept_lines), Decimal(0))
        budget = sum((line.budget for line in dept_lines), Decimal(0))
        contribution = sum((line.contribution for line in dept_lines), Decimal(0))
        dept_pct = ((actual - budget) / abs(budget) * 100) if budget != 0 else None
        rows.append(
            [
                department,
                str(len(dept_lines)),
                money(actual),
                money(budget),
                money(actual - budget),
                pct(dept_pct),
                money(contribution),
                "favourable" if contribution > 0 else ("unfavourable" if contribution < 0 else "flat"),
            ]
        )
    out.extend(
        render_table(
            ["department", "lines", "actual", "budget", "variance", "var %", "op income effect", "direction"],
            rows,
            "lrrrrrrl",
        )
    )
    out.append("")

    material: list[tuple[Line, Classification]] = []
    immaterial: list[Line] = []
    for line in lines:
        variance_pct = line.variance_pct
        hits_abs = abs(line.variance) >= materiality_abs
        hits_pct = variance_pct is not None and abs(variance_pct) >= materiality_pct
        if variance_pct is None:
            is_material = hits_abs  # no budget to take a percentage of
        elif rule == "and":
            is_material = hits_abs and hits_pct
        else:
            is_material = hits_abs or hits_pct
        if line.variance == 0:
            is_material = False
        if is_material:
            material.append((line, classify(line, volume_share, timing_share)))
        else:
            immaterial.append(line)

    material.sort(key=lambda pair: (-abs(pair[0].contribution), pair[0].department, pair[0].account_code))

    out.append(f"MATERIAL VARIANCES ({len(material)} line(s))")
    out.append("-" * 78)
    if material:
        rows = [
            [
                line.department,
                line.account_code,
                line.account_name,
                money(line.actual),
                money(line.budget),
                money(line.variance),
                pct(line.variance_pct),
                "F" if line.favourable else "U",
                classification.label,
                classification.source,
            ]
            for line, classification in material
        ]
        out.extend(
            render_table(
                ["department", "account", "name", "actual", "budget", "variance", "var %", "f/u", "class", "basis"],
                rows,
                "lllrrrrlll",
            )
        )
    else:
        out.append("No line breached the materiality thresholds.")
    out.append("")

    counts = {label: sum(1 for _, c in material if c.label == label) for label in CLASSIFICATIONS}
    out.append(
        "Classification mix: "
        + ", ".join(f"{label} {counts[label]}" for label in CLASSIFICATIONS)
    )
    out.append("")

    out.append("IMMATERIAL LINES (aggregated, not dropped)")
    out.append("-" * 78)
    if immaterial:
        immaterial_variance = sum((line.variance for line in immaterial), Decimal(0))
        immaterial_effect = sum((line.contribution for line in immaterial), Decimal(0))
        out.append(
            f"{len(immaterial)} line(s) below threshold, net variance {money(immaterial_variance)}, "
            f"operating income effect {money(immaterial_effect)}."
        )
        largest = sorted(immaterial, key=lambda line: (-abs(line.variance), line.department, line.account_code))[:3]
        for line in largest:
            out.append(
                f"  largest: {line.department} {line.account_code} {line.account_name} "
                f"{money(line.variance)} ({pct(line.variance_pct)})"
            )
    else:
        out.append("None: every line breached the thresholds.")
    out.append("")

    out.append("RECONCILIATION")
    out.append("-" * 78)
    material_effect = sum((line.contribution for line, _ in material), Decimal(0))
    immaterial_effect = sum((line.contribution for line in immaterial), Decimal(0))
    check_rows = [
        ["operating income effect of material lines", money(material_effect)],
        ["operating income effect of immaterial lines", money(immaterial_effect)],
        ["sum of all line effects", money(contribution_sum)],
        ["operating income variance from the P&L summary", money(operating_variance)],
        ["residual (must be 0.00)", money(residual)],
    ]
    if control is not None:
        check_rows.append(["operating income variance per --control-operating-variance", money(control)])
        check_rows.append(["control residual (must be 0.00)", money(control_residual)])
    out.extend(render_table(["check", "amount"], check_rows, "lr"))
    out.append("")

    out.append("DRAFT COMMENTARY (material lines only -- review before issuing)")
    out.append("-" * 78)
    if material:
        for index, (line, classification) in enumerate(material, start=1):
            out.append(f"{index}. [{classification.label.upper()}] {line.department} / {line.account_code}")
            out.append(f"   {draft_commentary(line, classification, period_label)}")
            out.append("")
    else:
        out.append("Nothing to comment on at the current thresholds.")
        out.append("")

    out.append("VERDICT")
    out.append("-" * 78)
    failures = []
    if residual != 0:
        failures.append(f"line effects minus P&L operating variance = {money(residual)}")
    if control is not None and control_residual != 0:
        failures.append(f"control residual {money(control_residual)}")
    if failures:
        out.append("DOES NOT TIE: " + "; ".join(failures) + ".")
        out.append("Check for rows excluded from the export or a department total that is not a sum of its lines.")
        out.append("Exit status 1.")
    else:
        out.append("Every line variance is accounted for in the operating income variance. Exit status 0.")

    payload = {
        "period": period_label,
        "asOf": as_of.isoformat(),
        "materiality": {
            "absolute": plain(materiality_abs),
            "percent": str(materiality_pct),
            "rule": rule,
        },
        "totals": {
            kind: {"actual": plain(totals[kind][0]), "budget": plain(totals[kind][1])}
            for kind in ACCOUNT_TYPES
        },
        "operatingIncome": {
            "actual": plain(actual_oi),
            "budget": plain(budget_oi),
            "variance": plain(operating_variance),
        },
        "material": [
            {
                "department": line.department,
                "accountCode": line.account_code,
                "accountName": line.account_name,
                "accountType": line.account_type,
                "actual": plain(line.actual),
                "budget": plain(line.budget),
                "variance": plain(line.variance),
                "variancePct": str(line.variance_pct.quantize(PCT, rounding=ROUND_HALF_UP))
                if line.variance_pct is not None
                else None,
                "favourable": line.favourable,
                "operatingIncomeEffect": plain(line.contribution),
                "classification": classification.label,
                "classificationBasis": classification.source,
                "evidence": classification.evidence,
                "volumeEffect": plain(classification.volume_effect)
                if classification.volume_effect is not None
                else None,
                "rateEffect": plain(classification.rate_effect)
                if classification.rate_effect is not None
                else None,
                "owner": line.owner,
                "commentary": draft_commentary(line, classification, period_label),
            }
            for line, classification in material
        ],
        "immaterial": {
            "lines": len(immaterial),
            "netVariance": plain(sum((line.variance for line in immaterial), Decimal(0))),
            "operatingIncomeEffect": plain(immaterial_effect),
        },
        "reconciliation": {
            "sumOfLineEffects": plain(contribution_sum),
            "operatingIncomeVariance": plain(operating_variance),
            "residual": plain(residual),
            "controlVariance": plain(control) if control is not None else None,
            "controlResidual": plain(control_residual) if control is not None else None,
        },
        "ties": not failures,
    }
    return out, payload, not failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Actual vs budget variance analysis with drafted commentary.")
    parser.add_argument("--actuals", required=True, help="actual vs budget CSV by department and account")
    parser.add_argument("--as-of", required=True, help="period end date, YYYY-MM-DD")
    parser.add_argument("--period-label", help="label used in commentary (default: month and year of --as-of)")
    parser.add_argument("--materiality-abs", default="5000", help="absolute materiality threshold (default 5000)")
    parser.add_argument("--materiality-pct", default="10", help="percentage materiality threshold (default 10)")
    parser.add_argument(
        "--materiality-rule",
        choices=("and", "or"),
        default="and",
        help="require both thresholds (default) or either one",
    )
    parser.add_argument(
        "--volume-share",
        default="60",
        help="percent of the variance the volume effect must explain to be classified volume (default 60)",
    )
    parser.add_argument(
        "--timing-share",
        default="50",
        help="year-to-date variance below this percent of the period variance reads as timing (default 50)",
    )
    parser.add_argument("--control-operating-variance", help="operating income variance per the P&L, as a control")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the report")
    args = parser.parse_args(argv)

    try:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            raise InputError(f"--as-of value {args.as_of!r} is not an ISO date (YYYY-MM-DD)") from None
        period_label = args.period_label or f"{MONTHS[as_of.month - 1]} {as_of.year}"

        def decimal_arg(value: str, label: str, *, minimum: Decimal | None = None) -> Decimal:
            try:
                parsed = Decimal(value.replace(",", ""))
            except InvalidOperation:
                raise InputError(f"{label} value {value!r} is not a number") from None
            if minimum is not None and parsed < minimum:
                raise InputError(f"{label} must be at least {minimum}")
            return parsed

        materiality_abs = decimal_arg(args.materiality_abs, "--materiality-abs", minimum=Decimal(0))
        materiality_pct = decimal_arg(args.materiality_pct, "--materiality-pct", minimum=Decimal(0))
        volume_share = decimal_arg(args.volume_share, "--volume-share", minimum=Decimal(0)) / 100
        timing_share = decimal_arg(args.timing_share, "--timing-share", minimum=Decimal(0)) / 100
        control = (
            decimal_arg(args.control_operating_variance, "--control-operating-variance")
            if args.control_operating_variance is not None
            else None
        )

        lines = load_lines(Path(args.actuals))
        report, payload, ties = build_report(
            lines, period_label, as_of, materiality_abs, materiality_pct,
            args.materiality_rule, volume_share, timing_share, control,
        )
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (DivisionByZero, InvalidOperation) as exc:  # defensive: never print a traceback at a user
        print(f"error: arithmetic failure while analysing the file ({exc})", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("\n".join(report))
    sys.stdout.flush()
    return 0 if ties else 1


if __name__ == "__main__":
    sys.exit(main())
