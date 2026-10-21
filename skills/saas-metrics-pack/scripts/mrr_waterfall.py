#!/usr/bin/env python3
"""MRR movement waterfall and the retention metrics that hang off it.

From a subscription-level export the script measures MRR at the opening date
and at the closing date, classifies every customer's movement between the two,
and reports:

    opening + new + expansion - contraction - churn + reactivation = closing

plus NRR, GRR, logo churn, the quick ratio, ARPA, and (when sales and
marketing spend is supplied) CAC and CAC payback.

The waterfall is reconciled against independently summed opening and closing
balances. If it does not tie exactly, the script prints the discrepancy and
exits non-zero.

Stdlib only, offline, deterministic.

Exit codes:
    0  the waterfall ties (and matches --expected-closing-mrr when supplied)
    1  it does not tie
    2  bad input (missing column, unparseable value, empty file, bad dates)
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
PCT = Decimal("0.1")
RATIO = Decimal("0.01")
REQUIRED = ("customer_id", "subscription_id", "mrr", "start_date", "end_date")

MOVEMENTS = ("new", "expansion", "contraction", "churn", "reactivation")


class InputError(Exception):
    """Raised for any malformed input; carries a message naming the field."""


@dataclass
class Subscription:
    customer_id: str
    customer_name: str
    subscription_id: str
    plan: str
    mrr: Decimal
    start_date: date
    end_date: date | None
    line: str

    def active_on(self, day: date) -> bool:
        if self.start_date > day:
            return False
        return self.end_date is None or self.end_date > day


@dataclass
class Customer:
    customer_id: str
    name: str
    subs: list[Subscription] = field(default_factory=list)
    opening: Decimal = Decimal(0)
    closing: Decimal = Decimal(0)
    movement: str = "flat"

    @property
    def delta(self) -> Decimal:
        return self.closing - self.opening


def money(value: Decimal) -> str:
    """Display form: thousands separators, 2 dp, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):,.2f}"


def plain(value: Decimal) -> str:
    """Machine form: 2 dp, no separators, ROUND_HALF_UP."""
    return f"{value.quantize(MONEY, rounding=ROUND_HALF_UP):.2f}"


def pct(value: Decimal) -> str:
    return f"{value.quantize(PCT, rounding=ROUND_HALF_UP)}%"


def logo_label(count: int) -> str:
    return f"{count} logo" if count == 1 else f"{count} logos"


def ratio(value: Decimal) -> str:
    return f"{value.quantize(RATIO, rounding=ROUND_HALF_UP)}"


def iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise InputError(f"{label} value {value!r} is not an ISO date (YYYY-MM-DD)") from None


def load_subscriptions(path: Path) -> list[Subscription]:
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    subs: list[Subscription] = []
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
            customer_id = row.get("customer_id") or ""
            if not customer_id:
                raise InputError(f"{path} line {line_no}: column 'customer_id' is empty")
            subscription_id = row.get("subscription_id") or ""
            if not subscription_id:
                raise InputError(f"{path} line {line_no}: column 'subscription_id' is empty")
            if subscription_id in seen:
                raise InputError(
                    f"{path} line {line_no}: duplicate subscription_id {subscription_id!r} "
                    f"(first seen on line {seen[subscription_id]})"
                )
            seen[subscription_id] = str(line_no)

            raw_mrr = (row.get("mrr") or "").replace(",", "").replace("$", "")
            if raw_mrr == "":
                raise InputError(f"{path} line {line_no}: column 'mrr' is empty")
            try:
                mrr = Decimal(raw_mrr)
            except InvalidOperation:
                raise InputError(
                    f"{path} line {line_no}: column 'mrr' value {row.get('mrr')!r} is not a number"
                ) from None
            if not mrr.is_finite():
                raise InputError(f"{path} line {line_no}: column 'mrr' value {row.get('mrr')!r} is not finite")
            if mrr < 0:
                raise InputError(
                    f"{path} line {line_no}: column 'mrr' is negative ({row.get('mrr')!r}); "
                    "net discounts into the subscription MRR instead"
                )

            start_raw = row.get("start_date") or ""
            if start_raw == "":
                raise InputError(f"{path} line {line_no}: column 'start_date' is empty")
            try:
                start_date = date.fromisoformat(start_raw)
            except ValueError:
                raise InputError(
                    f"{path} line {line_no}: column 'start_date' value {start_raw!r} is not an ISO date"
                ) from None

            end_raw = row.get("end_date") or ""
            end_date: date | None = None
            if end_raw:
                try:
                    end_date = date.fromisoformat(end_raw)
                except ValueError:
                    raise InputError(
                        f"{path} line {line_no}: column 'end_date' value {end_raw!r} is not an ISO date"
                    ) from None
                if end_date <= start_date:
                    raise InputError(
                        f"{path} line {line_no}: column 'end_date' ({end_raw}) is not after "
                        f"'start_date' ({start_raw})"
                    )

            subs.append(
                Subscription(
                    customer_id=customer_id,
                    customer_name=row.get("customer_name", "") or customer_id,
                    subscription_id=subscription_id,
                    plan=row.get("plan", ""),
                    mrr=mrr,
                    start_date=start_date,
                    end_date=end_date,
                    line=str(line_no),
                )
            )
    if not subs:
        raise InputError(f"{path}: no data rows found below the header")
    subs.sort(key=lambda sub: (sub.customer_id, sub.start_date, sub.subscription_id))
    return subs


def build_customers(subs: list[Subscription], opening_on: date, closing_on: date) -> list[Customer]:
    customers: dict[str, Customer] = {}
    for sub in subs:
        customer = customers.get(sub.customer_id)
        if customer is None:
            customer = Customer(sub.customer_id, sub.customer_name)
            customers[sub.customer_id] = customer
        elif customer.name == customer.customer_id and sub.customer_name != sub.customer_id:
            customer.name = sub.customer_name
        customer.subs.append(sub)
        if sub.active_on(opening_on):
            customer.opening += sub.mrr
        if sub.active_on(closing_on):
            customer.closing += sub.mrr

    ordered = [customers[key] for key in sorted(customers)]
    for customer in ordered:
        existed_before = any(sub.start_date < opening_on for sub in customer.subs)
        if customer.opening == 0 and customer.closing == 0:
            customer.movement = "no_impact"
        elif customer.opening == 0:
            customer.movement = "reactivation" if existed_before else "new"
        elif customer.closing == 0:
            customer.movement = "churn"
        elif customer.closing > customer.opening:
            customer.movement = "expansion"
        elif customer.closing < customer.opening:
            customer.movement = "contraction"
        else:
            customer.movement = "flat"
    return ordered


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
    customers: list[Customer],
    subs: list[Subscription],
    opening_on: date,
    closing_on: date,
    spend: Decimal | None,
    gross_margin: Decimal,
    expected_closing: Decimal | None,
    top: int,
) -> tuple[list[str], dict, bool]:
    out: list[str] = []

    # Independent control totals, summed straight off the subscription rows.
    control_opening = sum((sub.mrr for sub in subs if sub.active_on(opening_on)), Decimal(0))
    control_closing = sum((sub.mrr for sub in subs if sub.active_on(closing_on)), Decimal(0))

    opening = sum((customer.opening for customer in customers), Decimal(0))
    closing = sum((customer.closing for customer in customers), Decimal(0))

    buckets = {name: Decimal(0) for name in MOVEMENTS}
    logos = {name: 0 for name in MOVEMENTS}
    for customer in customers:
        if customer.movement == "new":
            buckets["new"] += customer.closing
            logos["new"] += 1
        elif customer.movement == "reactivation":
            buckets["reactivation"] += customer.closing
            logos["reactivation"] += 1
        elif customer.movement == "churn":
            buckets["churn"] += customer.opening
            logos["churn"] += 1
        elif customer.movement == "expansion":
            buckets["expansion"] += customer.delta
            logos["expansion"] += 1
        elif customer.movement == "contraction":
            buckets["contraction"] += -customer.delta
            logos["contraction"] += 1

    opening_logos = sum(1 for customer in customers if customer.opening > 0)
    closing_logos = sum(1 for customer in customers if customer.closing > 0)
    no_impact = [customer for customer in customers if customer.movement == "no_impact"]

    computed_closing = (
        opening + buckets["new"] + buckets["expansion"] + buckets["reactivation"]
        - buckets["contraction"] - buckets["churn"]
    )
    waterfall_residual = computed_closing - closing
    opening_residual = opening - control_opening
    closing_residual = closing - control_closing
    expected_residual = (closing - expected_closing) if expected_closing is not None else Decimal(0)

    out.append("SAAS METRICS PACK")
    out.append("=" * 74)
    out.append(f"Opening measured on : {opening_on.isoformat()}")
    out.append(f"Closing measured on : {closing_on.isoformat()}")
    out.append(f"Subscriptions read  : {len(subs)} across {len(customers)} customer(s)")
    out.append("")

    out.append("MRR MOVEMENT WATERFALL")
    out.append("-" * 74)
    rows = [["Opening MRR", money(opening), logo_label(opening_logos), ""]]
    for name in ("new", "expansion", "reactivation"):
        share = (buckets[name] / opening * 100) if opening else Decimal(0)
        rows.append([name.capitalize(), money(buckets[name]), logo_label(logos[name]), pct(share)])
    for name in ("contraction", "churn"):
        share = (buckets[name] / opening * 100) if opening else Decimal(0)
        rows.append([name.capitalize(), money(-buckets[name]), logo_label(logos[name]), pct(-share)])
    net_new = closing - opening
    rows.append(["Net new MRR", money(net_new), "", pct((net_new / opening * 100) if opening else Decimal(0))])
    rows.append(["Closing MRR", money(closing), logo_label(closing_logos), ""])
    out.extend(render_table(["movement", "mrr", "logos", "% of opening"], rows, "lrlr"))
    out.append("")

    display_sum = (
        opening.quantize(MONEY, rounding=ROUND_HALF_UP)
        + buckets["new"].quantize(MONEY, rounding=ROUND_HALF_UP)
        + buckets["expansion"].quantize(MONEY, rounding=ROUND_HALF_UP)
        + buckets["reactivation"].quantize(MONEY, rounding=ROUND_HALF_UP)
        - buckets["contraction"].quantize(MONEY, rounding=ROUND_HALF_UP)
        - buckets["churn"].quantize(MONEY, rounding=ROUND_HALF_UP)
    )
    if display_sum != closing.quantize(MONEY, rounding=ROUND_HALF_UP):
        out.append(
            f"Note: the rounded column above adds to {money(display_sum)}; the difference of "
            f"{money(display_sum - closing)} is display rounding only."
        )
        out.append("The reconciliation below uses unrounded values.")
        out.append("")

    out.append("RECONCILIATION")
    out.append("-" * 74)
    check_rows = [
        ["opening + new + expansion + reactivation - contraction - churn", money(computed_closing)],
        ["closing MRR from customer balances", money(closing)],
        ["residual (must be 0.00)", money(waterfall_residual)],
        ["opening MRR summed independently from subscription rows", money(control_opening)],
        ["opening residual (must be 0.00)", money(opening_residual)],
        ["closing MRR summed independently from subscription rows", money(control_closing)],
        ["closing residual (must be 0.00)", money(closing_residual)],
    ]
    if expected_closing is not None:
        check_rows.append(["closing MRR per --expected-closing-mrr", money(expected_closing)])
        check_rows.append(["control residual (must be 0.00)", money(expected_residual)])
    out.extend(render_table(["check", "amount"], check_rows, "lr"))
    out.append("")

    out.append("RETENTION AND EFFICIENCY")
    out.append("-" * 74)
    metric_rows: list[list[str]] = []
    if opening > 0:
        nrr = (opening + buckets["expansion"] - buckets["contraction"] - buckets["churn"]) / opening * 100
        grr = (opening - buckets["contraction"] - buckets["churn"]) / opening * 100
        metric_rows.append(["Net revenue retention (NRR)", pct(nrr), "excludes new logos and reactivations"])
        metric_rows.append(["Gross revenue retention (GRR)", pct(grr), "capped at 100% by construction"])
    else:
        nrr = grr = None
        metric_rows.append(["Net revenue retention (NRR)", "n/a", "opening MRR is zero"])
        metric_rows.append(["Gross revenue retention (GRR)", "n/a", "opening MRR is zero"])

    if opening_logos > 0:
        logo_churn = Decimal(logos["churn"]) / Decimal(opening_logos) * 100
        metric_rows.append(
            ["Logo churn", pct(logo_churn), f"{logos['churn']} of {opening_logos} opening logos"]
        )
    else:
        logo_churn = None
        metric_rows.append(["Logo churn", "n/a", "no opening logos"])

    downward = buckets["contraction"] + buckets["churn"]
    if downward > 0:
        quick = (buckets["new"] + buckets["expansion"] + buckets["reactivation"]) / downward
        metric_rows.append(
            ["Quick ratio", ratio(quick), "(new + expansion + reactivation) / (contraction + churn)"]
        )
    else:
        quick = None
        metric_rows.append(["Quick ratio", "n/a", "no contraction or churn in the period"])

    arpa_open = opening / opening_logos if opening_logos else None
    arpa_close = closing / closing_logos if closing_logos else None
    metric_rows.append(
        ["ARPA opening", money(arpa_open) if arpa_open is not None else "n/a", logo_label(opening_logos)]
    )
    metric_rows.append(
        ["ARPA closing", money(arpa_close) if arpa_close is not None else "n/a", logo_label(closing_logos)]
    )

    cac = None
    payback = None
    if spend is not None:
        if logos["new"] > 0:
            cac = spend / logos["new"]
            metric_rows.append(
                ["CAC", money(cac), f"{money(spend)} spend / {logo_label(logos['new'])}"]
            )
        else:
            metric_rows.append(["CAC", "n/a", "no new logos in the period"])
        contribution = buckets["new"] * gross_margin
        if contribution > 0:
            payback = spend / contribution
            metric_rows.append(
                [
                    "CAC payback",
                    f"{payback.quantize(RATIO, rounding=ROUND_HALF_UP)} months",
                    f"spend / (new MRR {money(buckets['new'])} x {pct(gross_margin * 100)} gross margin)",
                ]
            )
        else:
            metric_rows.append(
                ["CAC payback", "n/a", "new MRR x gross margin is zero, payback is undefined"]
            )
    else:
        metric_rows.append(["CAC", "n/a", "--sales-marketing-spend not supplied"])
        metric_rows.append(["CAC payback", "n/a", "--sales-marketing-spend not supplied"])
    out.extend(render_table(["metric", "value", "basis"], metric_rows, "lrl"))
    out.append("")

    out.append(f"LARGEST MOVEMENTS (top {top} by absolute MRR change)")
    out.append("-" * 74)
    movers = [customer for customer in customers if customer.movement not in ("flat", "no_impact")]
    movers.sort(key=lambda customer: (-abs(customer.delta), customer.customer_id))
    rows = [
        [
            customer.customer_id,
            customer.name,
            customer.movement,
            money(customer.opening),
            money(customer.closing),
            money(customer.delta),
        ]
        for customer in movers[:top]
    ]
    if rows:
        out.extend(
            render_table(["customer_id", "customer", "movement", "opening", "closing", "change"], rows, "lllrrr")
        )
    else:
        out.append("No customer changed MRR in the period.")
    out.append("")

    if no_impact:
        out.append("CUSTOMERS WITH NO WATERFALL IMPACT")
        out.append("-" * 74)
        out.append(
            "These customers had zero MRR at both measurement dates. They started and stopped inside"
        )
        out.append("the period, so they appear in neither new nor churn. Review them before quoting logo counts:")
        for customer in no_impact:
            out.append(f"  {customer.customer_id} {customer.name}")
        out.append("")

    out.append("VERDICT")
    out.append("-" * 74)
    failures = []
    if waterfall_residual != 0:
        failures.append(f"waterfall residual {money(waterfall_residual)}")
    if opening_residual != 0:
        failures.append(f"opening residual {money(opening_residual)}")
    if closing_residual != 0:
        failures.append(f"closing residual {money(closing_residual)}")
    if expected_closing is not None and expected_residual != 0:
        failures.append(f"control residual vs --expected-closing-mrr {money(expected_residual)}")
    if failures:
        out.append("DOES NOT TIE: " + "; ".join(failures) + ".")
        out.append("Do not publish these metrics. Check the subscription export for rows excluded by the")
        out.append("billing system's own MRR definition (trials, one-off fees, taxes, annual amortisation).")
        out.append("Exit status 1.")
    else:
        out.append("Waterfall ties exactly to the opening and closing balances. Exit status 0.")

    payload = {
        "openingOn": opening_on.isoformat(),
        "closingOn": closing_on.isoformat(),
        "openingMrr": plain(opening),
        "closingMrr": plain(closing),
        "netNewMrr": plain(net_new),
        "movements": {name: plain(buckets[name]) for name in MOVEMENTS},
        "movementLogos": logos,
        "openingLogos": opening_logos,
        "closingLogos": closing_logos,
        "nrrPct": str(nrr.quantize(PCT, rounding=ROUND_HALF_UP)) if nrr is not None else None,
        "grrPct": str(grr.quantize(PCT, rounding=ROUND_HALF_UP)) if grr is not None else None,
        "logoChurnPct": str(logo_churn.quantize(PCT, rounding=ROUND_HALF_UP)) if logo_churn is not None else None,
        "quickRatio": str(quick.quantize(RATIO, rounding=ROUND_HALF_UP)) if quick is not None else None,
        "arpaOpening": plain(arpa_open) if arpa_open is not None else None,
        "arpaClosing": plain(arpa_close) if arpa_close is not None else None,
        "cac": plain(cac) if cac is not None else None,
        "cacPaybackMonths": str(payback.quantize(RATIO, rounding=ROUND_HALF_UP)) if payback is not None else None,
        "noImpactCustomers": [customer.customer_id for customer in no_impact],
        "reconciliation": {
            "computedClosing": plain(computed_closing),
            "waterfallResidual": plain(waterfall_residual),
            "openingResidual": plain(opening_residual),
            "closingResidual": plain(closing_residual),
            "expectedClosing": plain(expected_closing) if expected_closing is not None else None,
            "expectedResidual": plain(expected_residual) if expected_closing is not None else None,
        },
        "ties": not failures,
    }
    return out, payload, not failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MRR waterfall and SaaS retention metrics.")
    parser.add_argument("--subscriptions", required=True, help="subscription-level CSV export")
    parser.add_argument("--period-start", required=True, help="opening measurement date, YYYY-MM-DD")
    parser.add_argument("--as-of", required=True, help="closing measurement date, YYYY-MM-DD")
    parser.add_argument("--sales-marketing-spend", help="S&M spend for the acquisition period, for CAC")
    parser.add_argument("--gross-margin-pct", default="100", help="gross margin percent for CAC payback (default 100)")
    parser.add_argument("--expected-closing-mrr", help="closing MRR per the billing system, as a control total")
    parser.add_argument("--top", type=int, default=5, help="how many movers to list (default 5)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the report")
    args = parser.parse_args(argv)

    try:
        opening_on = iso_date(args.period_start, "--period-start")
        closing_on = iso_date(args.as_of, "--as-of")
        if closing_on <= opening_on:
            raise InputError("--as-of must be after --period-start")
        if args.top < 1:
            raise InputError("--top must be at least 1")

        spend = None
        if args.sales_marketing_spend is not None:
            try:
                spend = Decimal(args.sales_marketing_spend.replace(",", ""))
            except InvalidOperation:
                raise InputError(
                    f"--sales-marketing-spend value {args.sales_marketing_spend!r} is not a number"
                ) from None
            if spend < 0:
                raise InputError("--sales-marketing-spend must not be negative")
        try:
            gross_margin = Decimal(args.gross_margin_pct) / 100
        except InvalidOperation:
            raise InputError(f"--gross-margin-pct value {args.gross_margin_pct!r} is not a number") from None
        if not 0 < gross_margin <= 1:
            raise InputError("--gross-margin-pct must be greater than 0 and at most 100")

        expected_closing = None
        if args.expected_closing_mrr is not None:
            try:
                expected_closing = Decimal(args.expected_closing_mrr.replace(",", ""))
            except InvalidOperation:
                raise InputError(
                    f"--expected-closing-mrr value {args.expected_closing_mrr!r} is not a number"
                ) from None

        subs = load_subscriptions(Path(args.subscriptions))
        customers = build_customers(subs, opening_on, closing_on)
        lines, payload, ties = build_report(
            customers, subs, opening_on, closing_on, spend, gross_margin, expected_closing, args.top
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
