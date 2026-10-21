#!/usr/bin/env python3
"""
Ecommerce Bounce Guard: analyzer.

Reads one bounce bundle (JSON, see DATA_CONTRACT.md) and prints a Markdown
report: which flows are bouncing, the first bad day, whether the bounced
recipients look like bot / fake-checkout traffic, and what to do next.

Local files only. Never calls an API, never changes anything.

    python3 scripts/bounce_audit.py --bundle examples/data/bundle.json \
        [--shopify-daily examples/data/shopify_daily.csv] [--json out.json]

Python 3.8+, standard library only. Output is deterministic for a given input.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import math
import re
import sys

WARN_RATE = 0.02        # >= 2% flow bounce rate -> WARNING
CRIT_RATE = 0.05        # >= 5% -> CRITICAL
SPIKE_MULT = 3.0        # >= 3x the flow's own baseline (and >= WARN) -> CRITICAL
MIN_WEEKLY = 25         # weekly buckets with fewer recipients are "low volume"
MIN_DAILY = 10          # daily buckets with fewer recipients are ignored for onset
ONSET_FLOOR = 0.05      # an onset day must bounce at least 5% ...
ONSET_MIN_BOUNCES = 5   # ... with at least 5 bounces, and stay high on the next days

FREE_MAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "icloud.com", "me.com", "aol.com", "msn.com", "proton.me",
    "protonmail.com", "gmx.com", "yandex.com", "mail.com",
}

TRIGGER_HINTS = (
    ("checkout", "Checkout Started"), ("cart", "Checkout Started / Added to Cart"),
    ("browse", "Viewed Product"), ("welcome", "List / form signup"),
    ("signup", "List / form signup"), ("popup", "List / form signup"),
)


class InputError(Exception):
    pass


# --------------------------------------------------------------------------- loading
def load_bundle(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            b = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise InputError(f"cannot read bundle {path}: {e}")
    for key in ("account", "as_of", "flows", "weekly", "daily"):
        if key not in b:
            raise InputError(f"bundle is missing required key '{key}'")
    try:
        dt.date.fromisoformat(b["as_of"])
    except ValueError:
        raise InputError("as_of must be YYYY-MM-DD")
    flow_ids = {f["id"] for f in b["flows"]}
    for series in ("weekly", "daily"):
        s = b[series]
        n = len(s.get("date_times", []))
        if n == 0:
            raise InputError(f"{series}.date_times is empty")
        for row in s.get("rows", []):
            if row.get("flow_id") not in flow_ids:
                raise InputError(f"{series} row references unknown flow_id {row.get('flow_id')!r}")
            for k in ("recipients", "bounced"):
                vals = row.get(k)
                if not isinstance(vals, list) or len(vals) != n:
                    raise InputError(f"{series} row {row.get('flow_id')}/{row.get('flow_message_id')}: "
                                     f"'{k}' must be a list of {n} numbers")
                if any((v or 0) < 0 for v in vals):
                    raise InputError(f"{series} row {row.get('flow_id')}: negative '{k}'")
            if any((b_ or 0) > (r or 0) for b_, r in zip(row["bounced"], row["recipients"])):
                raise InputError(f"{series} row {row.get('flow_id')}: bounced exceeds recipients")
    return b


def load_shopify_daily(path: str | None) -> list[dict]:
    if not path:
        return []
    out = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            for i, r in enumerate(csv.DictReader(f), start=2):
                try:
                    out.append({"date": dt.date.fromisoformat(r["date"].strip()).isoformat(),
                                "checkouts": int(r["abandoned_checkouts"]),
                                "orders": int(r["orders"])})
                except (KeyError, ValueError) as e:
                    raise InputError(f"{path} line {i}: {e}")
    except OSError as e:
        raise InputError(f"cannot read {path}: {e}")
    return sorted(out, key=lambda r: r["date"])


# --------------------------------------------------------------------------- math
def rate(b: float, r: float) -> float | None:
    return b / r if r else None


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def day(s: str) -> str:
    return s[:10]


def fmt_day(s: str) -> str:
    d = dt.date.fromisoformat(day(s))
    return f"{d.strftime('%b')} {d.day}"


def rollup(series: dict, by: str) -> dict:
    """Sum rows by flow_id (by='flow') or by (flow_id, flow_message_id)."""
    n = len(series["date_times"])
    agg: dict = {}
    for row in series.get("rows", []):
        key = row["flow_id"] if by == "flow" else (row["flow_id"], row.get("flow_message_id") or "")
        slot = agg.setdefault(key, {"recipients": [0] * n, "bounced": [0] * n})
        for k in ("recipients", "bounced"):
            for i, v in enumerate(row[k]):
                slot[k][i] += v or 0
    return agg


def classify(cur: float | None, base: float | None) -> str:
    if cur is None:
        return "LOW VOLUME"
    if cur >= CRIT_RATE:
        return "CRITICAL"
    if base and cur >= WARN_RATE and cur >= SPIKE_MULT * base:
        return "CRITICAL"
    if cur >= WARN_RATE:
        return "WARNING"
    return "OK"


def current_and_baseline(rec: list, bnc: list) -> tuple:
    """Current = last complete week with volume (last bucket, else the one before).
    Baseline = every week before the last two."""
    idx = len(rec) - 1
    if rec[idx] < MIN_WEEKLY and idx > 0:
        idx -= 1
    cur = rate(bnc[idx], rec[idx]) if rec[idx] >= MIN_WEEKLY else None
    base_r, base_b = sum(rec[:-2]), sum(bnc[:-2])
    base = rate(base_b, base_r) if base_r >= MIN_WEEKLY else None
    base_weeks = [rate(b, r) for b, r in zip(bnc[:-2], rec[:-2]) if r >= MIN_WEEKLY]
    return idx, cur, base, base_weeks


def find_onset(dates: list, rec: list, bnc: list, base: float | None) -> dict | None:
    threshold = max(ONSET_FLOOR, (base or 0) * SPIKE_MULT)

    def hot(i: int) -> bool:
        return rec[i] >= MIN_DAILY and bnc[i] >= ONSET_MIN_BOUNCES and bnc[i] / rec[i] >= threshold

    for i in range(len(dates)):
        after = [j for j in range(i + 1, min(i + 3, len(dates))) if rec[j] >= MIN_DAILY]
        sustained = all(hot(j) for j in after)  # a one-day blip is not an onset
        if hot(i) and sustained:
            j0 = max(0, i - 5)
            return {"index": i, "date": day(dates[i]), "recipients": rec[i], "bounced": bnc[i],
                    "rate": bnc[i] / rec[i], "prior_from": day(dates[j0]) if i > 0 else None,
                    "prior_to": day(dates[i - 1]) if i > 0 else None,
                    "prior_recipients": sum(rec[j0:i]), "prior_bounced": sum(bnc[j0:i])}
    return None


# --------------------------------------------------------------------------- fingerprint
def entropy(s: str) -> float:
    if not s:
        return 0.0
    c = collections.Counter(s)
    return -sum(v / len(s) * math.log2(v / len(s)) for v in c.values())


def bot_signals(email: str) -> list[str]:
    email = (email or "").strip().lower()
    if email.count("@") != 1:
        return ["malformed-address"]
    local, domain = email.split("@")
    sig = []
    digits = sum(ch.isdigit() for ch in local)
    if len(local) >= 10 and entropy(local) > 3.4 and not re.search(r"[aeiou]{1}[a-z]{2,}[aeiou]", local):
        sig.append("random-local-part")
    if digits >= 4 and digits / max(len(local), 1) > 0.35:
        sig.append("digit-heavy")
    if domain in FREE_MAIL and local.count(".") >= 3:
        sig.append("dot-stuffed-freemail")
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain):
        sig.append("invalid-domain")
    elif domain not in FREE_MAIL and re.search(r"\d{3,}|[bcdfghjklmnpqrstvwxz]{5,}", domain.split(".")[0]):
        sig.append("random-domain")
    return sig


def mask(email: str) -> str:
    email = email or ""
    if "@" not in email:
        return (email[:2] + "***") if email else "(none)"
    local, domain = email.split("@", 1)
    head = local[:2] if len(local) > 3 else local[:1]
    tail = local[-1:] if len(local) > 3 else ""
    return f"{head}***{tail}@{domain}"


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def fingerprint(bounces: list[dict], flows_by_id: dict) -> dict:
    n = len(bounces)
    if n == 0:
        return {"events": 0}
    domains, types, sources, signals = (collections.Counter() for _ in range(4))
    flagged = fresh = fresh_known = 0
    aos_known = aos_never = 0
    for ev in bounces:
        email = (ev.get("email") or "").lower()
        domains[email.split("@")[-1] if "@" in email else "(none)"] += 1
        types[(ev.get("bounce_type") or "unknown").lower()] += 1
        fid = ev.get("flow_id")
        sources[flows_by_id.get(fid, {}).get("name") if fid else (ev.get("campaign") or "(unknown)")] += 1
        s = bot_signals(email)
        for x in s:
            signals[x] += 1
        flagged += bool(s)
        t, c = parse_ts(ev.get("datetime")), parse_ts(ev.get("profile_created"))
        if t and c:
            fresh_known += 1
            fresh += (t - c) <= dt.timedelta(hours=48)
        if ev.get("active_on_site") is not None:
            aos_known += 1
            aos_never += not ev["active_on_site"]
    return {
        "events": n,
        "bot_signal_share": flagged / n,
        "fresh_share": (fresh / fresh_known) if fresh_known else None,
        "fresh_known": fresh_known,
        "never_active_share": (aos_never / aos_known) if aos_known else None,
        "active_known": aos_known,
        "hard_share": types.get("hard", 0) / n if types else None,
        "types": dict(sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))),
        "domains": sorted(domains.items(), key=lambda kv: (-kv[1], kv[0]))[:8],
        "sources": sorted(sources.items(), key=lambda kv: (-kv[1], str(kv[0])))[:6],
        "signals": dict(sorted(signals.items(), key=lambda kv: (-kv[1], kv[0]))),
        "samples": [mask(ev.get("email") or "") for ev in sorted(
            bounces, key=lambda e: (len(bot_signals(e.get("email") or "")) == 0, e.get("datetime") or ""))[:5]],
    }


def shopify_signal(rows: list[dict], onset: str | None) -> dict | None:
    if not rows or not onset:
        return None
    before = [r for r in rows if r["date"] < onset]
    after = [r for r in rows if r["date"] >= onset]
    if not before or not after:
        return None
    avg = lambda xs, k: sum(x[k] for x in xs) / len(xs)
    cb, ca = avg(before, "checkouts"), avg(after, "checkouts")
    ob, oa = avg(before, "orders"), avg(after, "orders")
    return {"checkouts_before": cb, "checkouts_after": ca, "orders_before": ob, "orders_after": oa,
            "checkout_mult": (ca / cb) if cb else None, "order_mult": (oa / ob) if ob else None,
            "days_before": len(before), "days_after": len(after)}


# --------------------------------------------------------------------------- verdict
def verdict(flagged: list, fp: dict, shop: dict | None, shared_onset: bool) -> tuple[str, list[str]]:
    reasons = []
    if any(f["onset"] for f in flagged):
        reasons.append("sudden step change on a single day, not a gradual creep")
    if shared_onset:
        reasons.append("several flows broke on the same day, which points to one upstream source")
    if fp.get("fresh_share") is not None and fp["fresh_share"] >= 0.6:
        reasons.append(f"{pct(fp['fresh_share'], 0)} of bounced profiles were created less than 48 hours before the bounce")
    if fp.get("never_active_share") is not None and fp["never_active_share"] >= 0.6:
        reasons.append(f"{pct(fp['never_active_share'], 0)} of sampled bounced profiles never fired Active on Site")
    if fp.get("bot_signal_share", 0) >= 0.4:
        reasons.append(f"{pct(fp['bot_signal_share'], 0)} of bounced addresses have machine-generated patterns")
    if shop and shop.get("checkout_mult") and shop["checkout_mult"] >= 2 and (shop.get("order_mult") or 0) < 1.3:
        reasons.append(f"Shopify abandoned checkouts rose {shop['checkout_mult']:.1f}x while orders stayed flat "
                       f"({shop['order_mult']:.2f}x)")
    if not flagged:
        return "HEALTHY", reasons
    strong = sum(1 for r in reasons if not r.startswith("sudden") and not r.startswith("several"))
    if any(f["onset"] for f in flagged) and strong >= 2:
        return "LIKELY BOT / FAKE-CHECKOUT TRAFFIC", reasons
    if strong >= 1:
        return "POSSIBLE BOT TRAFFIC — CONFIRM BEFORE ACTING", reasons
    return "LIKELY LIST-HYGIENE PROBLEM", reasons


# --------------------------------------------------------------------------- report
def analyze(b: dict, shopify_rows: list[dict]) -> dict:
    flows_by_id = {f["id"]: f for f in b["flows"]}
    msg_names = {m["id"]: m.get("name") or m["id"] for m in b.get("flow_messages", [])}
    wdates, ddates = b["weekly"]["date_times"], b["daily"]["date_times"]
    wflow, dflow = rollup(b["weekly"], "flow"), rollup(b["daily"], "flow")
    wmsg = rollup(b["weekly"], "message")

    results = []
    for fid in sorted(wflow, key=lambda k: flows_by_id[k]["name"]):
        rec, bnc = wflow[fid]["recipients"], wflow[fid]["bounced"]
        idx, cur, base, base_weeks = current_and_baseline(rec, bnc)
        sev = classify(cur, base)
        worst = None
        for (f2, mid), s in wmsg.items():
            if f2 != fid or not mid:
                continue
            r = rate(s["bounced"][idx], s["recipients"][idx]) if s["recipients"][idx] else None
            if r is not None and (worst is None or r > worst["rate"]):
                worst = {"id": mid, "name": msg_names.get(mid, mid), "rate": r,
                         "bounced": s["bounced"][idx], "recipients": s["recipients"][idx]}
        onset = None
        if sev in ("CRITICAL", "WARNING"):
            # Date the onset on the worst email when it has daily data, else on the whole flow.
            dmsg = rollup(b["daily"], "message").get((fid, worst["id"])) if worst else None
            src = dmsg or dflow.get(fid)
            if src:
                onset = find_onset(ddates, src["recipients"], src["bounced"], base)
                if onset:
                    onset["scope"] = worst["name"] if dmsg else r_name(flows_by_id, fid)
        results.append({
            "flow_id": fid, "name": flows_by_id[fid]["name"], "status": flows_by_id[fid].get("status") or "unknown",
            "trigger": flows_by_id[fid].get("trigger_type"), "severity": sev,
            "week": day(wdates[idx]), "cur": cur, "cur_b": bnc[idx], "cur_r": rec[idx],
            "base": base, "base_min": min(base_weeks) if base_weeks else None,
            "base_max": max(base_weeks) if base_weeks else None, "base_n": len(base_weeks),
            "onset": onset, "worst_message": worst,
        })
    order = {"CRITICAL": 0, "WARNING": 1, "OK": 2, "LOW VOLUME": 3}
    results.sort(key=lambda r: (order[r["severity"]], -(r["cur"] or 0), r["name"]))
    flagged = [r for r in results if r["severity"] in ("CRITICAL", "WARNING")]
    onsets = sorted({r["onset"]["date"] for r in flagged if r["onset"]})
    first_onset = onsets[0] if onsets else None
    shared = [r["name"] for r in flagged if r["onset"] and r["onset"]["date"] == first_onset]
    fp = fingerprint(b.get("bounces", []), flows_by_id)
    shop = shopify_signal(shopify_rows, first_onset)
    v, reasons = verdict(flagged, fp, shop, len(shared) >= 2)
    return {"account": b["account"], "as_of": b["as_of"], "source": b.get("source", "klaviyo"),
            "flows": results, "flagged": flagged, "first_onset": first_onset, "shared_onset": shared,
            "fingerprint": fp, "shopify": shop, "verdict": v, "reasons": reasons}


def r_name(flows_by_id: dict, fid: str) -> str:
    return flows_by_id[fid]["name"]


def trigger_label(r: dict) -> str:
    name = r["name"].lower()
    for hint, label in TRIGGER_HINTS:
        if hint in name:
            return label
    return r.get("trigger") or "its trigger"


def render(a: dict) -> str:
    L = []
    L.append(f"## Bounce Guard: {a['account']} ({a['source'].title()}), as of {a['as_of']}")
    L.append("")
    flagged = a["flagged"]
    if not flagged:
        L.append("**No flow is above the bounce thresholds.** Every flow with enough volume is under "
                 f"{pct(WARN_RATE, 0)} for its latest week. Nothing needs to change.")
    else:
        crit = [r for r in flagged if r["severity"] == "CRITICAL"]
        live = [r for r in flagged if (r["status"] or "").lower() == "live"]
        lead = crit[0] if crit else flagged[0]
        L.append(f"**{len(flagged)} flow(s) are bouncing above threshold, {len(crit)} critical, "
                 f"{len(live)} still live.**")
        L.append("")
        for r in flagged:
            wm = r["worst_message"]
            what = (f"{wm['name']} bounced {wm['bounced']:,} of {wm['recipients']:,} sends ({pct(wm['rate'])})"
                    if wm else f"{r['name']} bounced {r['cur_b']:,} of {r['cur_r']:,} sends ({pct(r['cur'])})")
            base = (f" The flow's baseline was {pct(r['base'])} (weekly range {pct(r['base_min'])}–"
                    f"{pct(r['base_max'])} over {r['base_n']} weeks)." if r["base"] is not None else "")
            L.append(f"- **{r['name']}** ({r['status']}): {what} in the week of {fmt_day(r['week'])}.{base}")
        if a["first_onset"]:
            o = next(r["onset"] for r in flagged if r["onset"] and r["onset"]["date"] == a["first_onset"])
            prior = (f" The days before were clean: {fmt_day(o['prior_from'])}–{fmt_day(o['prior_to'])} had "
                     f"{o['prior_bounced']:,} bounces out of {o['prior_recipients']:,} sends."
                     if o["prior_from"] else "")
            L.append("")
            L.append(f"**First bad day: {fmt_day(a['first_onset'])} ({a['first_onset']}).** "
                     f"{o['scope']}: {o['bounced']:,} of {o['recipients']:,} sends bounced ({pct(o['rate'])}).{prior}")
            if len(a["shared_onset"]) >= 2:
                L.append(f"The same day also broke: {', '.join(a['shared_onset'][1:])}.")
    L.append("")
    L.append(f"**Diagnosis: {a['verdict']}**")
    for r in a["reasons"]:
        L.append(f"- {r[0].upper() + r[1:]}")
    L.append("")
    L.append("| Severity | Flow | Status | Latest week | Bounced (all emails) | Baseline (weekly range) | First bad day |")
    L.append("| --- | --- | --- | --- | ---: | --- | --- |")
    for r in a["flows"]:
        if r["severity"] == "LOW VOLUME":
            continue
        rng = (f"{pct(r['base'])} ({pct(r['base_min'])}–{pct(r['base_max'])})"
               if r["base"] is not None else "—")
        od = f"{fmt_day(r['onset']['date'])} ({r['onset']['bounced']}/{r['onset']['recipients']})" if r["onset"] else "—"
        L.append(f"| {r['severity']} | {r['name']} | {r['status']} | {fmt_day(r['week'])} | "
                 f"{r['cur_b']:,}/{r['cur_r']:,} ({pct(r['cur'])}) | {rng} | {od} |")
    low = [r["name"] for r in a["flows"] if r["severity"] == "LOW VOLUME"]
    if low:
        L.append("")
        L.append(f"Not scored (under {MIN_WEEKLY} recipients in the latest week): {', '.join(low)}.")

    fp = a["fingerprint"]
    if fp.get("events"):
        L.append("")
        L.append(f"**Who is bouncing** ({fp['events']:,} bounce events)")
        hard = fp["types"].get("hard", 0)
        if len(fp["types"]) == 1:
            L.append(f"- Bounce type: all {next(iter(fp['types']))}")
        else:
            L.append(f"- Bounce type: {', '.join(f'{k} {v:,}' for k, v in fp['types'].items())}"
                     + (f" ({pct(hard / fp['events'], 0)} hard)" if hard else ""))
        if fp.get("fresh_share") is not None:
            L.append(f"- Profile created < 48h before bouncing: {pct(fp['fresh_share'], 0)} "
                     f"(of {fp['fresh_known']:,} with a creation date)")
        if fp.get("never_active_share") is not None:
            L.append(f"- Never fired Active on Site: {pct(fp['never_active_share'], 0)} "
                     f"(of {fp['active_known']:,} sampled)")
        L.append(f"- Machine-generated address patterns: {pct(fp['bot_signal_share'], 0)}"
                 + (f" ({', '.join(f'{k} {v}' for k, v in fp['signals'].items())})" if fp["signals"] else ""))
        L.append(f"- Top domains: {', '.join(f'{d} {c}' for d, c in fp['domains'])}")
        L.append(f"- Sources: {', '.join(f'{s} {c}' for s, c in fp['sources'])}")
        L.append(f"- Masked samples: {', '.join(fp['samples'])}")
    shop = a["shopify"]
    if shop:
        L.append("")
        L.append(f"**Shopify cross-check:** abandoned checkouts averaged {shop['checkouts_before']:.0f}/day for "
                 f"{shop['days_before']} days before the onset and {shop['checkouts_after']:.0f}/day after "
                 f"({shop['checkout_mult']:.1f}x). Orders went from {shop['orders_before']:.0f}/day to "
                 f"{shop['orders_after']:.0f}/day ({shop['order_mult']:.2f}x).")

    L.append("")
    L.append("**Recommended next steps (nothing has been changed)**")
    steps = []
    if flagged:
        names = ", ".join(r["name"] for r in flagged)
        bot = a["verdict"].startswith(("LIKELY BOT", "POSSIBLE BOT"))
        if bot:
            steps.append(f"Add flow filters to {names}: *Active on Site at least once in the last 30 days* and "
                         "*Bounced Email zero times over all time*. Add trigger filters *$value > 0* and "
                         "*Item Count ≥ 1*. This keeps real shoppers in the flow and drops scripted checkouts.")
        live_crit = [r for r in flagged if r["severity"] == "CRITICAL" and (r["status"] or "").lower() == "live"]
        if live_crit:
            targets = ", ".join(
                (f"\"{r['worst_message']['name']}\" ({pct(r['worst_message']['rate'])} bouncing)"
                 if r["worst_message"] else f"the first email of {r['name']}") for r in live_crit)
            steps.append(f"If the filters can't go in today, set {targets} to Manual so sends queue instead of "
                         "bouncing. Review and clear the queue before switching back to Live.")
        if bot:
            steps.append("Block the source in Shopify: confirm bot protection (hCaptcha) is on for checkout, "
                         "login and newsletter forms. Rate-limit /cart and /checkouts at the edge if it continues, "
                         f"and open a Shopify support ticket with the {fmt_day(a['first_onset']) if a['first_onset'] else 'onset'} date.")
            steps.append("Add real-time email validation at capture, before the first flow email. Suppression alone "
                         "won't fix this: hard bounces are already auto-suppressed and bots keep creating new addresses.")
        else:
            steps.append("Audit list sources and segment age for the flagged flows. Sunset profiles with no "
                         "engagement in 180 days, and confirm imports aren't feeding these flows.")
        steps.append("Until bounces are under 1% on new sends, send campaigns only to people engaged in the last 30 "
                     "days and grow volume no more than 20–30% per send day. Check Google Postmaster daily. "
                     "The full ramp plan is in references/playbook.md.")
        steps.append("Re-run this audit 48–72 hours after each change to confirm it worked.")
    else:
        steps.append("Keep this audit on a daily schedule through BFCM so a spike shows up on day one.")
    for i, s in enumerate(steps, 1):
        L.append(f"{i}. {s}")
    L.append("")
    L.append(f"_Thresholds: warning ≥ {pct(WARN_RATE, 0)}, critical ≥ {pct(CRIT_RATE, 0)} or ≥ "
             f"{SPIKE_MULT:.0f}x the flow's own baseline. Read-only: no flows, filters or profiles were changed._")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bundle", required=True, help="bounce bundle JSON (see DATA_CONTRACT.md)")
    ap.add_argument("--shopify-daily", help="optional CSV: date,abandoned_checkouts,orders")
    ap.add_argument("--json", help="also write the structured analysis to this path")
    args = ap.parse_args(argv)
    try:
        b = load_bundle(args.bundle)
        a = analyze(b, load_shopify_daily(args.shopify_daily))
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(render(a))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(a, f, indent=2, sort_keys=True, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
