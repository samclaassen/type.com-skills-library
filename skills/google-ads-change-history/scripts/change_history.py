#!/usr/bin/env python3
"""What changed on a client's Google Ads account, and who changed it.

Read-only. Runs GAQL SELECT queries against `change_event` (the last 30 days:
who, which tool, old -> new values) and, when the requested window reaches
further back, `change_status` (days 31-90: which resources changed, no actor).
Nothing is ever mutated and nothing is written to disk; the report goes to
stdout.

Adapted from fourteenwm/ppc-ai-skills `change-history-checker` (MIT). See
NOTICE.md for the list of modifications.

Examples:
    # One client, last 30 days (live API)
    python3 scripts/change_history.py --client harbor-and-vine --days 30

    # Portfolio view across every client profile, last 14 days
    python3 scripts/change_history.py --all-clients --days 14

    # Offline: recorded rows instead of the API (deterministic)
    python3 scripts/change_history.py --client harbor-and-vine --days 90 \\
        --clients-dir examples/clients --fixture examples/fixture_change_events.json

Credentials (live mode only) are read by the official google-ads client
library from, in order: --google-ads-config PATH, the
GOOGLE_ADS_CONFIGURATION_FILE_PATH env var, GOOGLE_ADS_* env vars (when
GOOGLE_ADS_DEVELOPER_TOKEN is set), ./google-ads.yaml, then ~/google-ads.yaml.
Secrets are never printed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

# Pinned explicitly. v20 and v21 are already shut off; v25 sunsets August 2027
# per https://developers.google.com/google-ads/api/docs/sunset-dates.
# Bump together with google-ads in requirements.txt.
API_VERSION = "v25"

EVENT_WINDOW_DAYS = 30  # change_event: date range must be within the past 30 days
STATUS_WINDOW_DAYS = 90  # change_status: date range must be within the past 90 days
MAX_LIMIT = 10000  # API maximum for LIMIT on both resources
MAX_PAGES = 10  # continuation queries per resource per client (<= 100k rows)

CLIENT_TYPES = {
    "GOOGLE_ADS_WEB_CLIENT": "Web UI",
    "GOOGLE_ADS_EDITOR": "Google Ads Editor",
    "GOOGLE_ADS_API": "API",
    "GOOGLE_ADS_SCRIPTS": "Google Ads scripts",
    "GOOGLE_ADS_AUTOMATED_RULE": "Automated rule",
    "GOOGLE_ADS_RECOMMENDATIONS": "Auto-applied recommendation",
    "GOOGLE_ADS_RECOMMENDATIONS_SUBSCRIPTION": "Recommendations auto-apply subscription",
    "GOOGLE_ADS_BULK_UPLOAD": "Bulk upload",
    "GOOGLE_ADS_MOBILE_APP": "Mobile app",
    "SEARCH_ADS_360_SYNC": "Search Ads 360 sync",
    "SEARCH_ADS_360_POST": "Search Ads 360 post",
    "INTERNAL_TOOL": "Google internal tool",
    "OTHER": "Other",
    "UNKNOWN": "Unknown tool",
    "UNSPECIFIED": "Unknown tool",
}
AUTOMATED_CLIENT_TYPES = {
    "GOOGLE_ADS_AUTOMATED_RULE",
    "GOOGLE_ADS_RECOMMENDATIONS",
    "GOOGLE_ADS_RECOMMENDATIONS_SUBSCRIPTION",
    "GOOGLE_ADS_SCRIPTS",
    "INTERNAL_TOOL",
}

RESOURCE_LABELS = {
    "CAMPAIGN": "Campaign",
    "CAMPAIGN_BUDGET": "Budget",
    "AD_GROUP": "Ad group",
    "AD_GROUP_AD": "Ad",
    "AD": "Ad",
    "AD_GROUP_CRITERION": "Keyword/criterion",
    "CAMPAIGN_CRITERION": "Campaign criterion",
    "AD_GROUP_BID_MODIFIER": "Bid adjustment",
    "BIDDING_STRATEGY": "Bid strategy",
    "ASSET": "Asset",
    "AD_GROUP_ASSET": "Ad group asset",
    "CAMPAIGN_ASSET": "Campaign asset",
    "CUSTOMER_ASSET": "Account asset",
    "ASSET_SET": "Asset set",
    "ASSET_SET_ASSET": "Asset set asset",
    "CAMPAIGN_ASSET_SET": "Campaign asset set",
    "FEED": "Feed",
    "FEED_ITEM": "Feed item",
    "AD_GROUP_FEED": "Ad group feed",
    "CAMPAIGN_FEED": "Campaign feed",
}

FIELD_LABELS = {
    "amount_micros": "budget amount",
    "status": "status",
    "cpc_bid_micros": "max CPC",
    "cpm_bid_micros": "max CPM",
    "cpv_bid_micros": "max CPV",
    "bid_modifier": "bid adjustment",
    "bidding_strategy_type": "bid strategy",
    "target_cpa.target_cpa_micros": "target CPA",
    "maximize_conversions.target_cpa_micros": "target CPA",
    "target_roas.target_roas": "target ROAS",
    "maximize_conversion_value.target_roas": "target ROAS",
    "target_spend.cpc_bid_ceiling_micros": "max CPC limit",
    "campaign_budget": "assigned budget",
    "name": "name",
    "keyword.text": "keyword",
    "keyword.match_type": "match type",
    "final_urls": "final URL",
    "bidding_strategy": "portfolio bid strategy",
    "network_settings.target_google_search": "Google Search",
    "network_settings.target_search_network": "Search partners",
    "network_settings.target_content_network": "Display Network",
    "network_settings.target_partner_search_network": "Google partner network",
    "network_settings.target_youtube": "YouTube",
    "network_settings.target_google_tv_network": "Google TV network",
    "geo_target_type_setting.positive_geo_target_type": "location targeting option",
    "geo_target_type_setting.negative_geo_target_type": "location exclusion option",
    "location.geo_target_constant": "location",
    "proximity.radius": "radius",
    "language.language_constant": "language",
    "start_date": "start date",
    "end_date": "end date",
    "start_date_time": "start date",
    "end_date_time": "end date",
    "advertising_channel_type": "campaign type",
}

ENUM_LABELS = {
    "PRESENCE_OR_INTEREST": "presence or interest",
    "PRESENCE": "presence only",
    "SEARCH_INTEREST": "search interest",
}

BID_MARKERS = (
    "bid",
    "target_cpa",
    "target_roas",
    "bidding_strategy",
    "maximize_",
    "manual_cp",
    "target_spend",
    "target_impression_share",
    "percent_cpc",
)
BID_RESOURCES = {"BIDDING_STRATEGY", "AD_GROUP_BID_MODIFIER"}
# Material campaign settings: which networks, locations, languages and dates the
# campaign serves on. Matched on the first segment of each changed field path.
SETTING_ROOTS = {
    "network_settings",
    "geo_target_type_setting",
    "location",
    "location_group",
    "proximity",
    "language",
    "ad_schedule",
    "start_date",
    "end_date",
    "start_date_time",
    "end_date_time",
    "advertising_channel_type",
}
CATEGORY_ORDER = ("BUDGET", "BID", "STATUS", "SETTING")
HIGHLIGHT_CATEGORIES = ("BUDGET", "BID", "SETTING")


# --------------------------------------------------------------------------
# Client profiles (CLIENTS.md contract)
# --------------------------------------------------------------------------


class ProfileError(Exception):
    pass


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def load_profiles(clients_dir: Path) -> "OrderedDict[str, dict]":
    if not clients_dir.is_dir():
        raise ProfileError(
            f"No client profiles found: {clients_dir}/ does not exist. Create "
            f"{clients_dir}/<client-slug>/client.json (see CLIENTS.md), or pass "
            f"--clients-dir."
        )
    profiles: "OrderedDict[str, dict]" = OrderedDict()
    for child in sorted(clients_dir.iterdir()):
        path = child / "client.json"
        if not child.is_dir() or not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProfileError(f"{path} is not valid JSON ({exc}).") from exc
        if not data.get("name"):
            raise ProfileError(f'{path} is missing the required "name" field.')
        profiles[child.name] = data
    return profiles


def resolve_client(profiles: "OrderedDict[str, dict]", query: str, clients_dir: Path):
    if query in profiles:
        return query, profiles[query]
    wanted = _norm(query)
    matches = [
        (slug, p) for slug, p in profiles.items() if _norm(slug) == wanted or _norm(p["name"]) == wanted
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        known = ", ".join(profiles) or "none"
        raise ProfileError(
            f'No client profile matches "{query}". Known slugs: {known}. Add '
            f"{clients_dir}/<client-slug>/client.json with at least a \"name\" field "
            f"(see CLIENTS.md)."
        )
    raise ProfileError(f'"{query}" matches more than one profile; use the slug.')


def customer_ids(profile: dict):
    accounts = profile.get("accounts") or {}
    cid = re.sub(r"\D", "", str(accounts.get("google_ads_customer_id") or ""))
    login = re.sub(r"\D", "", str(accounts.get("google_ads_login_customer_id") or ""))
    return cid, login


def dashed(cid: str) -> str:
    return f"{cid[:3]}-{cid[3:6]}-{cid[6:]}" if len(cid) == 10 else cid


def missing_cid_message(slug: str, clients_dir: Path) -> str:
    return (
        f'accounts.google_ads_customer_id is missing. Add it to '
        f'{clients_dir}/{slug}/client.json, e.g. "accounts": '
        f'{{"google_ads_customer_id": "123-456-7890"}}; add '
        f'"google_ads_login_customer_id" too if access is through a manager (MCC) account.'
    )


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


def plan_windows(as_of: dt.date, start: dt.date, end: dt.date):
    """Split [start, end] (inclusive dates) into a change_event part and a
    change_status part, honoring each resource's API window."""
    event_floor = as_of - dt.timedelta(days=EVENT_WINDOW_DAYS - 1)
    event = None
    status = None
    if end >= event_floor:
        event = (max(start, event_floor), end)
    if start < event_floor:
        status = (start, min(end, event_floor - dt.timedelta(days=1)))
    return event, status


# --------------------------------------------------------------------------
# Row sources: fixture or live API. Both yield the same normalized dicts.
# --------------------------------------------------------------------------


def _in_window(ts: str, window) -> bool:
    day = dt.date.fromisoformat(ts[:10])
    return window[0] <= day <= window[1]


class FixtureSource:
    def __init__(self, path: Path):
        self.data = json.loads(path.read_text(encoding="utf-8"))
        self.as_of = dt.date.fromisoformat(self.data["as_of"])

    def fetch(self, cid: str, login: str, event_window, status_window):
        account = (self.data.get("customers") or {}).get(cid)
        if account is None:
            return None
        events = [
            r for r in account.get("change_event", []) if event_window and _in_window(r["change_date_time"], event_window)
        ]
        statuses = [
            r
            for r in account.get("change_status", [])
            if status_window and _in_window(r["last_change_date_time"], status_window)
        ]
        return {
            "currency_code": account.get("currency_code", ""),
            "change_event": events,
            "change_status": statuses,
            "truncated": [],
        }


class LiveSource:
    """Read-only access through the official google-ads client library."""

    def __init__(self, config_path: str | None):
        try:
            from google.ads.googleads.client import GoogleAdsClient
            from google.ads.googleads.errors import GoogleAdsException
        except ImportError:
            sys.exit(
                "The google-ads package is not installed. Run: "
                "pip install -r requirements.txt (or use --fixture for an offline run)."
            )
        self.GoogleAdsException = GoogleAdsException
        if config_path:
            self.client = GoogleAdsClient.load_from_storage(config_path, version=API_VERSION)
        elif os.environ.get("GOOGLE_ADS_CONFIGURATION_FILE_PATH"):
            self.client = GoogleAdsClient.load_from_storage(version=API_VERSION)
        elif os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"):
            self.client = GoogleAdsClient.load_from_env(version=API_VERSION)
        elif Path("google-ads.yaml").is_file():
            self.client = GoogleAdsClient.load_from_storage("google-ads.yaml", version=API_VERSION)
        else:
            self.client = GoogleAdsClient.load_from_storage(version=API_VERSION)  # ~/google-ads.yaml
        self.default_login = self.client.login_customer_id
        self.service = self.client.get_service("GoogleAdsService", version=API_VERSION)
        self.as_of = dt.date.today()

    def _search(self, cid: str, query: str):
        return self.service.search(customer_id=cid, query=query)

    def _paged(self, cid: str, build_query, ts_of, key_of):
        """Run a DESC-ordered query, continuing from the last timestamp when a
        page hits the 10,000-row LIMIT (Google's documented continuation)."""
        rows, seen, upper, truncated = [], set(), None, False
        for _ in range(MAX_PAGES):
            batch = list(self._search(cid, build_query(upper)))
            fresh = [r for r in batch if key_of(r) not in seen]
            for r in fresh:
                seen.add(key_of(r))
            rows.extend(fresh)
            if len(batch) < MAX_LIMIT or not fresh:
                break
            upper = ts_of(batch[-1])
        else:
            truncated = True
        return rows, truncated

    def fetch(self, cid: str, login: str, event_window, status_window):
        self.client.login_customer_id = login or self.default_login
        out = {"currency_code": "", "change_event": [], "change_status": [], "truncated": []}
        try:
            if event_window:
                lo = event_window[0].isoformat()
                hi = (event_window[1] + dt.timedelta(days=1)).isoformat()

                def q_event(upper):
                    bound = f"change_event.change_date_time <= '{upper}'" if upper else f"change_event.change_date_time < '{hi}'"
                    return (
                        "SELECT change_event.resource_name, change_event.change_date_time, "
                        "change_event.change_resource_name, change_event.user_email, "
                        "change_event.client_type, change_event.change_resource_type, "
                        "change_event.old_resource, change_event.new_resource, "
                        "change_event.resource_change_operation, change_event.changed_fields, "
                        "change_event.campaign, change_event.ad_group, campaign.name, "
                        "ad_group.name, customer.currency_code "
                        "FROM change_event "
                        f"WHERE change_event.change_date_time >= '{lo}' AND {bound} "
                        f"ORDER BY change_event.change_date_time DESC LIMIT {MAX_LIMIT}"
                    )

                rows, trunc = self._paged(
                    cid, q_event, lambda r: r.change_event.change_date_time, lambda r: r.change_event.resource_name
                )
                if trunc:
                    out["truncated"].append("change_event")
                for row in rows:
                    out["currency_code"] = out["currency_code"] or row.customer.currency_code
                    out["change_event"].append(normalize_event(row))
            if status_window:
                lo = status_window[0].isoformat()
                hi = (status_window[1] + dt.timedelta(days=1)).isoformat()

                def q_status(upper):
                    bound = (
                        f"change_status.last_change_date_time <= '{upper}'"
                        if upper
                        else f"change_status.last_change_date_time < '{hi}'"
                    )
                    return (
                        "SELECT change_status.resource_name, change_status.resource_type, "
                        "change_status.resource_status, change_status.last_change_date_time, "
                        "campaign.name, ad_group.name "
                        "FROM change_status "
                        f"WHERE change_status.last_change_date_time >= '{lo}' AND {bound} "
                        f"ORDER BY change_status.last_change_date_time DESC LIMIT {MAX_LIMIT}"
                    )

                rows, trunc = self._paged(
                    cid,
                    q_status,
                    lambda r: r.change_status.last_change_date_time,
                    lambda r: r.change_status.resource_name,
                )
                if trunc:
                    out["truncated"].append("change_status")
                for row in rows:
                    cs = row.change_status
                    out["change_status"].append(
                        {
                            "last_change_date_time": cs.last_change_date_time,
                            "resource_type": cs.resource_type.name,
                            "resource_status": cs.resource_status.name,
                            "resource_name": cs.resource_name,
                            "campaign_name": row.campaign.name,
                            "ad_group_name": row.ad_group.name,
                        }
                    )
        except self.GoogleAdsException as ex:
            codes = sorted({str(e.error_code).strip().replace("\n", " ") for e in ex.failure.errors})
            messages = sorted({e.message for e in ex.failure.errors})
            raise RuntimeError(
                f"Google Ads API error (request id {ex.request_id}): {'; '.join(codes)} - {' '.join(messages)}"
            ) from None
        return out


def _get_path(obj, path: str):
    for seg in path.split("."):
        if obj is None:
            return None
        if hasattr(obj, seg):
            obj = getattr(obj, seg)
        elif hasattr(obj, seg + "_"):  # reserved words, e.g. type -> type_
            obj = getattr(obj, seg + "_")
        else:
            return None
    return obj


def _plain(value):
    """Convert a proto-plus value into JSON-friendly plain data."""
    if value is None:
        return None
    if hasattr(value, "name") and isinstance(value, int) and not isinstance(value, bool):
        return value.name  # enum
    if isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(type(value), "to_dict"):
        return type(value).to_dict(value)
    try:
        return [_plain(v) for v in value]
    except TypeError:
        return str(value)


def normalize_event(row) -> dict:
    ev = row.change_event
    rtype = ev.change_resource_type.name
    attr = rtype.lower()
    old = getattr(ev.old_resource, attr, None)
    new = getattr(ev.new_resource, attr, None)
    fields = list(ev.changed_fields.paths)
    record = {
        "change_date_time": ev.change_date_time,
        "user_email": ev.user_email,
        "client_type": ev.client_type.name,
        "change_resource_type": rtype,
        "resource_change_operation": ev.resource_change_operation.name,
        "change_resource_name": ev.change_resource_name,
        "campaign_name": row.campaign.name,
        "ad_group_name": row.ad_group.name,
        "entity_name": "",
        "changed_fields": fields,
        "old": {f: _plain(_get_path(old, f)) for f in fields} if old is not None else {},
        "new": {f: _plain(_get_path(new, f)) for f in fields} if new is not None else {},
    }
    for source in (new, old):
        for ident in ("name", "keyword.text", "text_asset.text"):
            value = _get_path(source, ident) if source is not None else None
            if value and not record["entity_name"]:
                record["entity_name"] = str(value)
    return record


# --------------------------------------------------------------------------
# Classification and formatting
# --------------------------------------------------------------------------


def categories(ev: dict) -> list:
    rtype = ev["change_resource_type"]
    fields = ev.get("changed_fields") or []
    cats = []
    if rtype == "CAMPAIGN_BUDGET" or any("budget" in f for f in fields):
        cats.append("BUDGET")
    if rtype in BID_RESOURCES or any(m in f for f in fields for m in BID_MARKERS):
        cats.append("BID")
    op = ev["resource_change_operation"]
    if op == "REMOVE" or (op == "UPDATE" and any(f == "status" or f.endswith(".status") for f in fields)):
        cats.append("STATUS")
    if any(f.split(".")[0] in SETTING_ROOTS for f in fields):
        cats.append("SETTING")
    return cats


def fmt_value(field: str, value, currency: str) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None or value == "" or value == []:
        return "(none)"
    if isinstance(value, str) and value in ENUM_LABELS:
        return ENUM_LABELS[value]
    if field.endswith("_micros"):
        try:
            amount = f"{int(value) / 1_000_000:,.2f}"
        except (TypeError, ValueError):
            return str(value)
        return f"{amount} {currency}".strip()
    if field.endswith("target_roas"):
        try:
            return f"{float(value) * 100:.0f}%"
        except (TypeError, ValueError):
            return str(value)
    if field == "bid_modifier":
        try:
            return f"{(float(value) - 1) * 100:+.0f}%"
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value)


def entity(ev: dict) -> str:
    label = RESOURCE_LABELS.get(ev["change_resource_type"], ev["change_resource_type"].replace("_", " ").capitalize())
    name = ev.get("entity_name") or ""
    if ev["change_resource_type"] == "CAMPAIGN":
        name = ev.get("campaign_name") or name
    elif ev["change_resource_type"] == "AD_GROUP":
        name = ev.get("ad_group_name") or name
    where = []
    if ev.get("campaign_name") and ev["change_resource_type"] != "CAMPAIGN":
        where.append(ev["campaign_name"])
    if ev.get("ad_group_name") and ev["change_resource_type"] not in ("CAMPAIGN", "AD_GROUP"):
        where.append(ev["ad_group_name"])
    text = f'{label} "{name}"' if name else label
    if where:
        text += f" in {' > '.join(where)}"
    return text


def describe(ev: dict, currency: str, max_fields: int = 3) -> str:
    op = ev["resource_change_operation"]
    if op == "REMOVE":
        return f"{entity(ev)}: removed"
    fields = [f for f in ev.get("changed_fields") or [] if f != "resource_name"]
    if op == "CREATE":
        shown = [f for f in fields if f in FIELD_LABELS and f not in ("name", "keyword.text")][:max_fields]
        parts = [f"{FIELD_LABELS[f]} {fmt_value(f, ev['new'].get(f), currency)}" for f in shown]
        return f"{entity(ev)}: created" + (f" ({'; '.join(parts)})" if parts else "")
    parts = []
    for f in fields[:max_fields]:
        label = FIELD_LABELS.get(f, f)
        parts.append(f"{label} {fmt_value(f, ev['old'].get(f), currency)} -> {fmt_value(f, ev['new'].get(f), currency)}")
    extra = len(fields) - max_fields
    if extra > 0:
        parts.append(f"+{extra} more field(s)")
    return f"{entity(ev)}: " + ("; ".join(parts) if parts else "updated")


def actor(ev: dict) -> str:
    email = ev.get("user_email") or ""
    tool = CLIENT_TYPES.get(ev.get("client_type", ""), ev.get("client_type", "Unknown tool"))
    return email if email else f"(no user email: {tool})"


def tool_label(ev: dict) -> str:
    return CLIENT_TYPES.get(ev.get("client_type", ""), ev.get("client_type", ""))


def day_label(day: str) -> str:
    return f"{day} ({dt.date.fromisoformat(day).strftime('%a')})"


def tag(cats: list) -> str:
    return f"[{'+'.join(cats)}]" if cats else "[other]"


def count_cats(events):
    counts = Counter()
    for ev in events:
        for c in categories(ev):
            counts[c] += 1
        if not categories(ev):
            counts["other"] += 1
    return counts


def cats_summary(counts: Counter) -> str:
    return ", ".join(f"{c.lower()} {counts.get(c, 0)}" for c in (*CATEGORY_ORDER, "other"))


def sort_key(ev):
    return (ev["change_date_time"], ev.get("_client", ""), ev.get("change_resource_name", ""))


# --------------------------------------------------------------------------
# Report sections
# --------------------------------------------------------------------------


def print_timeline(events, currency_of, label_clients: bool):
    print("TIMELINE BY DAY (oldest first; times in each account's time zone)")
    if not events:
        print("  No change_event rows in this window.")
        return
    current = None
    for ev in sorted(events, key=sort_key):
        day = ev["change_date_time"][:10]
        if day != current:
            print(f"\n{day_label(day)}")
            current = day
        prefix = f"[{ev['_client']}] " if label_clients else ""
        who = f"{ev['user_email']} via {tool_label(ev)}" if ev.get("user_email") else actor(ev)
        print(f"  {ev['change_date_time'][11:16]}  {prefix}{tag(categories(ev)):<9} {who} | {describe(ev, currency_of(ev))}")


def print_by_user(events, label_clients: bool):
    print("BY USER")
    if not events:
        print("  No change_event rows in this window.")
        return
    groups: dict = {}
    for ev in events:
        key = (ev["_client"] if label_clients else "", actor(ev))
        groups.setdefault(key, []).append(ev)
    for key in sorted(groups, key=lambda k: (k[0], -len(groups[k]), k[1])):
        evs = sorted(groups[key], key=sort_key)
        tools = sorted({tool_label(e) for e in evs})
        days = sorted({e["change_date_time"][:10] for e in evs})
        prefix = f"[{key[0]}] " if label_clients else ""
        span = days[0] if len(days) == 1 else f"{days[0]} to {days[-1]}"
        via = f"; via {', '.join(tools)}" if key[1].count("@") else ""
        print(f"  {prefix}{key[1]}: {len(evs)} change(s) ({cats_summary(count_cats(evs))}); {span}{via}")


def print_status_section(statuses, window, label_clients: bool):
    print(
        f"OLDER CHANGES {window[0]} to {window[1]} (change_status: which resources changed, "
        f"no user, no old/new values)"
    )
    if not statuses:
        print("  No change_status rows in this part of the window.")
        return
    groups = Counter()
    names: dict = {}
    for s in statuses:
        key = (s["last_change_date_time"][:10], s.get("_client", ""), s["resource_type"], s["resource_status"])
        groups[key] += 1
        label = s.get("campaign_name") or ""
        if label:
            names.setdefault(key, set()).add(label)
    for key in sorted(groups):
        day, client, rtype, status = key
        prefix = f"[{client}] " if label_clients else ""
        where = f" (campaigns: {', '.join(sorted(names[key]))})" if key in names else ""
        print(f"  {day_label(day)}  {prefix}{rtype} {status}: {groups[key]} resource(s){where}")


def escalation(slug: str, profile: dict, events, statuses, currency: str, event_window, status_window, truncated):
    name = profile["name"]
    print(f"ESCALATION SUMMARY: {name} (DRAFT for account lead review; not sent to the client)")
    website = (profile.get("website") or "").lower()
    client_domain = re.sub(r"^https?://(www\.)?", "", website).split("/")[0]
    counts = count_cats(events)
    lines = []
    if event_window:
        lines.append(
            f"{len(events)} change(s) with user and old/new values between {event_window[0]} and "
            f"{event_window[1]} ({cats_summary(counts)})."
        )
    if status_window:
        lines.append(
            f"{len(statuses)} resource(s) changed between {status_window[0]} and {status_window[1]}; "
            f"the API does not say who made those changes."
        )
    money = [
        e for e in sorted(events, key=sort_key) if any(c in HIGHLIGHT_CATEGORIES for c in categories(e))
    ]
    for ev in money[:10]:
        lines.append(
            f"  {tag(categories(ev))} {ev['change_date_time'][:16]} {actor(ev)}: {describe(ev, currency)}"
        )
    if len(money) > 10:
        lines.append(f"  ... and {len(money) - 10} more budget/bid/setting change(s) in the timeline above.")
    paused = [e for e in events if "STATUS" in categories(e)]
    if paused:
        by_new = Counter()
        for e in paused:
            new = "REMOVED" if e["resource_change_operation"] == "REMOVE" else str(e["new"].get("status", "changed"))
            by_new[new] += 1
        lines.append("Status changes: " + ", ".join(f"{k.lower()} {v}" for k, v in sorted(by_new.items())) + ".")
    people = Counter(actor(e) for e in events if e.get("user_email"))
    if people:
        lines.append("Who: " + ", ".join(f"{p} ({n})" for p, n in sorted(people.items(), key=lambda x: (-x[1], x[0]))) + ".")
    auto = Counter(tool_label(e) for e in events if e.get("client_type") in AUTOMATED_CLIENT_TYPES)
    if auto:
        lines.append(
            "Automated sources: "
            + ", ".join(f"{t} ({n})" for t, n in sorted(auto.items()))
            + ". Check these were intended before attributing them to a person."
        )
    no_email = [e for e in events if not e.get("user_email") and e.get("client_type") not in AUTOMATED_CLIENT_TYPES]
    if no_email:
        lines.append(f"{len(no_email)} change(s) have no user email; confirm in the Google Ads UI Change history.")
    if client_domain:
        client_side = sorted({e["user_email"] for e in events if e.get("user_email", "").endswith("@" + client_domain)})
        if client_side:
            lines.append(f"Made from the client's own domain ({client_domain}): {', '.join(client_side)}.")
    weekend = sorted({e["change_date_time"][:10] for e in events if dt.date.fromisoformat(e["change_date_time"][:10]).weekday() >= 5})
    if weekend:
        lines.append(f"Weekend activity on: {', '.join(weekend)}.")
    for resource in truncated:
        lines.append(f"WARNING: {resource} hit the row cap; narrow the window and re-run before relying on totals.")
    for line in lines:
        print(line if line.startswith("  ") else f"- {line}")

    # Suggested client-facing wording, deliberately neutral.
    print("\nSuggested reply (draft, edit before sending):")
    parts = []
    if event_window:
        money_bits = []
        if counts.get("BUDGET"):
            money_bits.append(f"{counts['BUDGET']} to budgets")
        if counts.get("BID"):
            money_bits.append(f"{counts['BID']} to bidding")
        if counts.get("STATUS"):
            money_bits.append(f"{counts['STATUS']} pausing, enabling or removing items")
        if counts.get("SETTING"):
            money_bits.append(
                f"{counts['SETTING']} to campaign settings such as networks, locations or languages"
            )
        detail = f" ({', '.join(money_bits)})" if money_bits else ""
        parts.append(
            f"Between {event_window[0]} and {event_window[1]} the account's change history shows "
            f"{len(events)} change(s){detail}."
        )
        named = sum(people.values())
        if named:
            parts.append(f"{named} were made by named users ({', '.join(sorted(people))}).")
        if auto:
            parts.append(f"{sum(auto.values())} came from Google Ads automation ({', '.join(sorted(auto))}), not a person.")
    if status_window:
        parts.append(
            f"For {status_window[0]} to {status_window[1]} the history records which items changed "
            f"({len(statuses)}) but not who changed them; Google Ads does not keep that detail "
            "for changes older than 30 days."
        )
    parts.append("Happy to walk through any of these.")
    print(f'  "{" ".join(parts)}"')


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=(
            "Read-only Google Ads change history for one client (or a labelled portfolio view): "
            "who changed what, grouped by day and by user, with budget/bid/status highlights."
        )
    )
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--client", help="client slug or name from clients/<slug>/client.json")
    who.add_argument("--all-clients", action="store_true", help="portfolio view across every client profile")
    p.add_argument("--clients-dir", default="clients", help="directory holding <slug>/client.json (default: clients)")
    p.add_argument("--days", type=int, default=30, help="look-back window in days, 1-90 (default: 30)")
    p.add_argument("--start", help="window start YYYY-MM-DD (overrides --days; must be within 90 days)")
    p.add_argument("--end", help="window end YYYY-MM-DD, inclusive (default: today / fixture as_of)")
    p.add_argument("--fixture", help="read recorded rows from this JSON file instead of calling the API")
    p.add_argument("--as-of", help="fixture mode only: treat this date as today (default: fixture as_of)")
    p.add_argument(
        "--google-ads-config",
        help="path to google-ads.yaml (otherwise GOOGLE_ADS_* env vars, ./google-ads.yaml, ~/google-ads.yaml)",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    clients_dir = Path(args.clients_dir)
    try:
        profiles = load_profiles(clients_dir)
        if args.client:
            slug, profile = resolve_client(profiles, args.client, clients_dir)
            targets = [(slug, profile)]
        else:
            targets = list(profiles.items())
            if not targets:
                raise ProfileError(f"No client profiles under {clients_dir}/ (see CLIENTS.md).")
    except ProfileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.fixture:
        source = FixtureSource(Path(args.fixture))
        if args.as_of:
            source.as_of = dt.date.fromisoformat(args.as_of)
    else:
        if args.as_of:
            print("ERROR: --as-of only applies to --fixture runs; the API window is relative to today.", file=sys.stderr)
            return 2
        if args.client and not customer_ids(targets[0][1])[0]:
            print(f"ERROR: {targets[0][1]['name']}: {missing_cid_message(targets[0][0], clients_dir)}", file=sys.stderr)
            return 2
        source = LiveSource(args.google_ads_config)
    as_of = source.as_of

    end = dt.date.fromisoformat(args.end) if args.end else as_of
    if args.start:
        start = dt.date.fromisoformat(args.start)
    else:
        if not 1 <= args.days <= STATUS_WINDOW_DAYS:
            print(f"ERROR: --days must be between 1 and {STATUS_WINDOW_DAYS} (the API keeps 90 days).", file=sys.stderr)
            return 2
        start = end - dt.timedelta(days=args.days - 1)
    oldest = as_of - dt.timedelta(days=STATUS_WINDOW_DAYS - 1)
    if start < oldest:
        print(
            f"ERROR: {start} is more than 90 days before {as_of}. The API cannot see that far back; "
            f"use Tools > Change history in the Google Ads UI (keeps about 2 years, exports CSV).",
            file=sys.stderr,
        )
        return 2
    if end > as_of or end < start:
        print("ERROR: --end must be on or before today and not before the start date.", file=sys.stderr)
        return 2
    event_window, status_window = plan_windows(as_of, start, end)
    portfolio = bool(args.all_clients)

    all_events, all_statuses, currencies, results, skipped = [], [], {}, [], []
    for slug, profile in targets:
        cid, login = customer_ids(profile)
        if not cid:
            skipped.append((slug, profile["name"], missing_cid_message(slug, clients_dir)))
            continue
        try:
            data = source.fetch(cid, login, event_window, status_window)
        except RuntimeError as exc:
            skipped.append((slug, profile["name"], str(exc)))
            continue
        if data is None:
            skipped.append((slug, profile["name"], f"fixture has no recorded rows for customer {dashed(cid)}"))
            continue
        for row in data["change_event"]:
            row["_client"] = slug
        for row in data["change_status"]:
            row["_client"] = slug
        currencies[slug] = data["currency_code"]
        all_events.extend(data["change_event"])
        all_statuses.extend(data["change_status"])
        results.append((slug, profile, cid, data))

    rule = "=" * 78
    print(rule)
    if portfolio:
        print("Google Ads change history: PORTFOLIO VIEW (every row labelled [client-slug])")
    else:
        slug, profile = targets[0]
        cid = customer_ids(profile)[0]
        print(f"Google Ads change history: {profile['name']} ({dashed(cid) if cid else 'no customer ID'})")
    print(f"Window: {start} to {end} ({(end - start).days + 1} days, as of {as_of})")
    if event_window:
        print(f"  {event_window[0]} to {event_window[1]}: change_event (who, which tool, old -> new values)")
    if status_window:
        print(f"  {status_window[0]} to {status_window[1]}: change_status only (older than 30 days; no user data)")
    print(f"Source: {'recorded fixture ' + Path(args.fixture).name if args.fixture else 'Google Ads API ' + API_VERSION}")
    print("Read-only: nothing in any account was changed. DRAFT for account lead review.")
    print(rule)

    if not results and skipped:
        for slug, name, why in skipped:
            print(f"SKIPPED {name} [{slug}]: {why}")
        return 1

    if portfolio:
        print("\nPORTFOLIO SUMMARY")
        for slug, profile, cid, data in results:
            evs = data["change_event"]
            people = {e["user_email"] for e in evs if e.get("user_email")}
            automated = {tool_label(e) for e in evs if e.get("client_type") in AUTOMATED_CLIENT_TYPES}
            unattributed = [
                e for e in evs if not e.get("user_email") and e.get("client_type") not in AUTOMATED_CLIENT_TYPES
            ]
            who = f"{len(people)} {'person' if len(people) == 1 else 'people'}, {len(automated)} automated source(s)"
            if unattributed:
                who += f", {len(unattributed)} change(s) with no user email"
            print(
                f"  [{slug}] {profile['name']} ({dashed(cid)}): {len(evs)} change(s) ({cats_summary(count_cats(evs))}); "
                f"{who}; {len(data['change_status'])} older resource change(s)"
            )
        for slug, name, why in skipped:
            print(f"  [{slug}] {name}: SKIPPED - {why}")

    print()
    print_timeline(all_events, lambda ev: currencies.get(ev["_client"], ""), portfolio)
    print()
    print_by_user(all_events, portfolio)
    if status_window:
        print()
        print_status_section(all_statuses, status_window, portfolio)
    for slug, profile, cid, data in results:
        print()
        escalation(
            slug,
            profile,
            data["change_event"],
            data["change_status"],
            currencies.get(slug, ""),
            event_window,
            status_window,
            data["truncated"],
        )
    if not portfolio:
        for slug, name, why in skipped:
            print(f"\nSKIPPED {name} [{slug}]: {why}")
    print()
    print(
        "Limits: change_event keeps 30 days and may omit some changes shown in the Google Ads UI; "
        "change_status keeps 90 days, one row per resource dated by its latest change. "
        "Older history: Google Ads UI > Tools > Change history."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
