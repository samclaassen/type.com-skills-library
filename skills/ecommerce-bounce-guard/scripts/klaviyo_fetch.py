#!/usr/bin/env python3
"""
Ecommerce Bounce Guard: Klaviyo fetcher.

Pulls flow bounce statistics and recent bounce events from the Klaviyo API
and writes a bounce bundle (see DATA_CONTRACT.md) for scripts/bounce_audit.py.

READ-ONLY: only GET requests plus the POST that Klaviyo's reporting endpoint
requires to *read* a report. It never pauses flows, edits filters, or
suppresses profiles.

    KLAVIYO_API_KEY=pk_xxx python3 scripts/klaviyo_fetch.py \
        --account "My Store" --out bundle.json \
        [--as-of 2026-10-05] [--weeks 12] [--days 21] \
        [--max-bounces 2000] [--aos-sample 50] [--key-env KLAVIYO_API_KEY]

Private key scopes needed: flows:read, metrics:read, events:read, profiles:read.

Rate limits (API revision 2026-07-15): flow-series reports allow 1/s burst,
2/min steady and 225/day. This script makes two report calls (weekly and
daily), waits between them, and retries 429s using Retry-After.

Python 3.8+, standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("KLAVIYO_API_BASE", "https://a.klaviyo.com/api").rstrip("/")
REVISION = os.environ.get("KLAVIYO_REVISION", "2026-07-15")
REPORT_GAP_SECONDS = float(os.environ.get("KLAVIYO_REPORT_GAP_SECONDS", "31"))


class FetchError(Exception):
    pass


class Client:
    def __init__(self, key: str):
        self.key = key
        self.calls = 0

    def request(self, method: str, url: str, body: dict | None = None, retries: int = 6) -> dict:
        if not url.startswith("http"):
            url = API + url
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(retries):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", f"Klaviyo-API-Key {self.key}")
            req.add_header("accept", "application/vnd.api+json")
            req.add_header("revision", REVISION)
            if data is not None:
                req.add_header("content-type", "application/vnd.api+json")
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    wait = e.headers.get("Retry-After")
                    wait_s = float(wait) if wait and wait.replace(".", "", 1).isdigit() else min(60, 5 * 2 ** attempt)
                    print(f"  HTTP {e.code}, retrying in {wait_s:.0f}s", file=sys.stderr)
                    time.sleep(wait_s)
                    continue
                detail = e.read().decode("utf-8", "replace")[:600]
                hint = ""
                if e.code in (401, 403):
                    hint = " (check the key is a private key with flows:read, metrics:read, events:read, profiles:read)"
                raise FetchError(f"HTTP {e.code} on {method} {url.split('?')[0]}{hint}: {detail}")
            except urllib.error.URLError as e:
                if attempt < retries - 1:
                    time.sleep(3)
                    continue
                raise FetchError(f"network error on {method} {url.split('?')[0]}: {e.reason}")
        raise FetchError(f"gave up after {retries} attempts: {method} {url.split('?')[0]}")

    def get_all(self, path: str, params: dict | None = None, limit: int | None = None):
        url = API + path + ("?" + urllib.parse.urlencode(params, safe='(),"*:[]') if params else "")
        n = 0
        while url:
            page = self.request("GET", url)
            yield page
            n += len(page.get("data", []))
            if limit and n >= limit:
                return
            url = (page.get("links") or {}).get("next")


# --------------------------------------------------------------------------- pieces
def find_metric(c: Client, name: str) -> str | None:
    for page in c.get_all("/metrics", {"fields[metric]": "name"}):
        for m in page.get("data", []):
            if (m.get("attributes") or {}).get("name") == name:
                return m["id"]
    return None


def list_flows(c: Client) -> list[dict]:
    out = []
    for page in c.get_all("/flows", {"fields[flow]": "name,status,trigger_type,archived"}):
        for f in page.get("data", []):
            a = f.get("attributes") or {}
            if a.get("archived"):
                continue
            out.append({"id": f["id"], "name": a.get("name") or f["id"],
                        "status": a.get("status"), "trigger_type": a.get("trigger_type")})
    return sorted(out, key=lambda f: f["id"])


def flow_series(c: Client, conv_metric: str, interval: str, start: dt.datetime, end: dt.datetime) -> dict:
    body = {"data": {"type": "flow-series-report", "attributes": {
        "statistics": ["recipients", "bounced"],
        "timeframe": {"start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")},
        "interval": interval,
        "conversion_metric_id": conv_metric,
    }}}
    url = API + "/flow-series-reports"
    dates: list = []
    rows: list = []
    while url:
        resp = c.request("POST", url, body)
        attrs = ((resp.get("data") or {}).get("attributes") or {})
        if not dates:
            dates = attrs.get("date_times") or []
        for res in attrs.get("results") or []:
            g = res.get("groupings") or {}
            if (g.get("send_channel") or "email") != "email":
                continue
            s = res.get("statistics") or {}
            rec = [int(v or 0) for v in (s.get("recipients") or [])][: len(dates)]
            bnc = [int(v or 0) for v in (s.get("bounced") or [])][: len(dates)]
            rec += [0] * (len(dates) - len(rec))
            bnc += [0] * (len(dates) - len(bnc))
            bnc = [min(b, r) for b, r in zip(bnc, rec)]
            rows.append({"flow_id": g.get("flow_id"), "flow_message_id": g.get("flow_message_id"),
                         "recipients": rec, "bounced": bnc})
        url = (resp.get("links") or {}).get("next")
    return {"date_times": dates, "rows": rows}


def message_names(c: Client, ids: list[str], cap: int = 40) -> list[dict]:
    out = []
    for mid in ids[:cap]:
        try:
            r = c.request("GET", f"/flow-messages/{urllib.parse.quote(mid)}?fields[flow-message]=name")
            out.append({"id": mid, "name": ((r.get("data") or {}).get("attributes") or {}).get("name") or mid})
        except FetchError as e:
            print(f"  could not name message {mid}: {e}", file=sys.stderr)
            out.append({"id": mid, "name": mid})
        time.sleep(0.4)
    return out


def bounce_events(c: Client, metric: str, since: dt.datetime, limit: int, flow_ids: set) -> list[dict]:
    params = {
        "filter": f'and(equals(metric_id,"{metric}"),greater-or-equal(datetime,{since.strftime("%Y-%m-%dT%H:%M:%SZ")}))',
        "include": "profile",
        "fields[profile]": "email,created",
        "sort": "-datetime",
    }
    out = []
    for page in c.get_all("/events", params, limit=limit):
        profiles = {p["id"]: (p.get("attributes") or {}) for p in page.get("included") or []
                    if p.get("type") == "profile"}
        for ev in page.get("data", []):
            a = ev.get("attributes") or {}
            props = a.get("event_properties") or {}
            pid = (((ev.get("relationships") or {}).get("profile") or {}).get("data") or {}).get("id")
            prof = profiles.get(pid, {})
            flow = props.get("$flow")
            btype = props.get("Bounce Type") or props.get("bounce_type") or props.get("$bounce_type")
            out.append({
                "datetime": a.get("datetime"),
                "profile_id": pid,
                "email": prof.get("email"),
                "profile_created": prof.get("created"),
                "bounce_type": str(btype).lower() if btype else None,
                "flow_id": flow if flow in flow_ids else None,
                "campaign": props.get("Campaign Name"),
                "active_on_site": None,
            })
            if len(out) >= limit:
                return out
    return out


def sample_active_on_site(c: Client, bounces: list[dict], aos_metric: str | None, sample: int) -> None:
    if not aos_metric or sample <= 0:
        return
    seen = set()
    for ev in bounces:
        pid = ev.get("profile_id")
        if not pid or pid in seen:
            continue
        if len(seen) >= sample:
            break
        seen.add(pid)
        q = urllib.parse.urlencode({
            "filter": f'and(equals(metric_id,"{aos_metric}"),equals(profile_id,"{pid}"))',
            "fields[event]": "datetime",
        }, safe='(),"')
        r = c.request("GET", f"/events?{q}")
        active = bool(r.get("data"))
        for e2 in bounces:
            if e2.get("profile_id") == pid:
                e2["active_on_site"] = active
        time.sleep(0.1)


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch a Klaviyo bounce bundle (read-only).")
    ap.add_argument("--account", required=True, help="store or account label for the report")
    ap.add_argument("--out", required=True, help="where to write the bundle JSON")
    ap.add_argument("--as-of", help="last day to include, YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--weeks", type=int, default=12)
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--max-bounces", type=int, default=2000)
    ap.add_argument("--aos-sample", type=int, default=50,
                    help="how many bounced profiles to check for any Active on Site event (0 to skip)")
    ap.add_argument("--conversion-metric", default="Placed Order",
                    help="metric name Klaviyo requires for reports (default: Placed Order)")
    ap.add_argument("--key-env", default="KLAVIYO_API_KEY", help="env var holding the private API key")
    args = ap.parse_args(argv)

    key = os.environ.get(args.key_env)
    if not key:
        print(f"error: set {args.key_env} to a Klaviyo private API key (read scopes only)", file=sys.stderr)
        return 2
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.datetime.now(dt.timezone.utc).date()
    end = dt.datetime.combine(as_of + dt.timedelta(days=1), dt.time(0), tzinfo=dt.timezone.utc)
    c = Client(key)
    try:
        print("Resolving metrics…", file=sys.stderr)
        conv = find_metric(c, args.conversion_metric)
        if not conv:
            raise FetchError(f"no '{args.conversion_metric}' metric in this account; pass --conversion-metric")
        bounced_metric = find_metric(c, "Bounced Email")
        aos_metric = find_metric(c, "Active on Site")
        print("Listing flows…", file=sys.stderr)
        flows = list_flows(c)
        flow_ids = {f["id"] for f in flows}
        print("Weekly flow report…", file=sys.stderr)
        weekly = flow_series(c, conv, "weekly", end - dt.timedelta(weeks=args.weeks), end)
        time.sleep(REPORT_GAP_SECONDS)
        print("Daily flow report…", file=sys.stderr)
        daily = flow_series(c, conv, "daily", end - dt.timedelta(days=args.days), end)
        known = {f["id"] for f in flows}
        for s in (weekly, daily):
            s["rows"] = [r for r in s["rows"] if r["flow_id"] in known]
        bouncing_msgs = sorted({r["flow_message_id"] for r in weekly["rows"]
                                if r.get("flow_message_id") and sum(r["bounced"]) > 0})
        print(f"Naming {min(len(bouncing_msgs), 40)} bouncing messages…", file=sys.stderr)
        msgs = message_names(c, bouncing_msgs)
        bounces = []
        if bounced_metric:
            print("Reading bounce events…", file=sys.stderr)
            bounces = bounce_events(c, bounced_metric, end - dt.timedelta(days=args.days),
                                    args.max_bounces, flow_ids)
            print(f"Sampling Active on Site for up to {args.aos_sample} bounced profiles…", file=sys.stderr)
            sample_active_on_site(c, bounces, aos_metric, args.aos_sample)
    except FetchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    for ev in bounces:
        ev.pop("profile_id", None)
    bundle = {
        "account": args.account, "as_of": as_of.isoformat(), "source": "klaviyo",
        "generated_by": "scripts/klaviyo_fetch.py", "api_revision": REVISION,
        "flows": flows, "flow_messages": msgs, "weekly": weekly, "daily": daily, "bounces": bounces,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=1)
    print(f"Wrote {args.out}: {len(flows)} flows, {len(weekly['rows'])} weekly rows, "
          f"{len(bounces)} bounce events, {c.calls} API calls.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
