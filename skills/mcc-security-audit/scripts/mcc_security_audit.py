#!/usr/bin/env python3
"""MCC security audit: read-only, agency-wide scan of a Google Ads manager tree.

Walks every account under one login manager (MCC) account and reads, per
account:
  customer_manager_link            which managers are linked (all statuses)
  customer_user_access             which users have access, role, grant date
  customer_user_access_invitation  open invitations

Each child account is mapped back to a client via clients/*/client.json
(accounts.google_ads_customer_id). Findings are severity-ranked, carry exact
account/manager IDs and a recommended manual action.

This script never mutates anything: it issues GoogleAdsService.search calls
only. It does not remove links, users, or invitations.

Modes:
  live     credentials from google-ads.yaml (--config) or GOOGLE_ADS_* env vars
  fixture  --fixture rows.json (recorded rows, no network, no credentials)

Output: markdown to stdout (default) or --json. Optional --csv-dir and
--sheet-id (gspread) exports.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# Pinned Google Ads API version. v20 sunset 2026-06-10, v21 sunset 2026-08-05.
# v25 (released 2026-07-22) is supported until August 2027 per
# https://developers.google.com/google-ads/api/docs/sunset-dates
# and is available in google-ads==32.0.0 (see requirements.txt).
GOOGLE_ADS_API_VERSION = "v25"

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
LIVE_LINK_STATUSES = {"ACTIVE", "PENDING"}
WRITE_ROLES = {"ADMIN", "STANDARD"}

# Stable ordering of finding kinds inside one severity (triage order).
KIND_ORDER = [
    "hostile_manager_link",
    "unknown_admin_user",
    "external_manager_pending",
    "external_manager_active",
    "unknown_admin_invitation",
    "unknown_write_user",
    "hostile_manager_history",
    "external_manager_probing",
    "unknown_invitation",
    "too_many_admins",
    "unmapped_account",
    "ambiguous_client_mapping",
    "scan_error",
    "unknown_readonly_user",
    "external_manager_history",
    "admin_without_passkey",
    "mapped_account_not_in_tree",
    "recent_access_change",
]
KIND_RANK = {k: i for i, k in enumerate(KIND_ORDER)}

WALK_QUERY = """
    SELECT
        customer_client.id,
        customer_client.descriptive_name,
        customer_client.status,
        customer_client.manager,
        customer_client.level
    FROM customer_client
"""
LINK_QUERY = """
    SELECT
        customer_manager_link.manager_customer,
        customer_manager_link.manager_link_id,
        customer_manager_link.status
    FROM customer_manager_link
"""
USER_QUERY = """
    SELECT
        customer_user_access.user_id,
        customer_user_access.email_address,
        customer_user_access.access_role,
        customer_user_access.access_creation_date_time,
        customer_user_access.inviter_user_email_address,
        customer_user_access.passkey_enabled
    FROM customer_user_access
"""
INVITE_QUERY = """
    SELECT
        customer_user_access_invitation.invitation_id,
        customer_user_access_invitation.email_address,
        customer_user_access_invitation.access_role,
        customer_user_access_invitation.invitation_status,
        customer_user_access_invitation.creation_date_time
    FROM customer_user_access_invitation
"""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def norm_cid(value) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def fmt_cid(cid: str) -> str:
    cid = norm_cid(cid)
    if len(cid) == 10:
        return f"{cid[:3]}-{cid[3:6]}-{cid[6:]}"
    return cid


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in (email or "") else ""


def domain_matches(domain: str, trusted: str) -> bool:
    trusted = trusted.lower().lstrip("@").lstrip(".")
    return domain == trusted or domain.endswith("." + trusted)


def parse_day(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def die(msg: str, code: int = 2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------
# Inputs: clients, allowlist, hostile list
# --------------------------------------------------------------------------

def load_clients(clients_dir: Path) -> dict:
    """Map normalized customer ID -> list of {slug, name, domain}."""
    if not clients_dir.is_dir():
        die(
            f"clients directory not found: {clients_dir}. Create clients/<client-slug>/client.json "
            "per CLIENTS.md with accounts.google_ads_customer_id for each client, or pass --clients-dir."
        )
    by_cid: dict[str, list[dict]] = defaultdict(list)
    missing_id: list[str] = []
    profiles = 0
    for path in sorted(clients_dir.glob("*/client.json")):
        slug = path.parent.name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            die(f"{path} is not valid JSON ({exc})")
        profiles += 1
        name = data.get("name") or slug
        website = data.get("website") or ""
        host = urlparse(website if "://" in website else f"https://{website}").hostname or ""
        domain = host.lower()[4:] if host.lower().startswith("www.") else host.lower()
        cid = norm_cid((data.get("accounts") or {}).get("google_ads_customer_id"))
        if not cid:
            missing_id.append(slug)
            continue
        by_cid[cid].append({"slug": slug, "name": name, "domain": domain})
    if profiles == 0:
        die(f"no clients/*/client.json profiles found under {clients_dir} (see CLIENTS.md).")
    return {"by_cid": dict(by_cid), "missing_id": missing_id, "profiles": profiles}


def load_allowlist(path: str | None) -> dict:
    empty = {"managers": {}, "domains": [], "emails": set()}
    if not path:
        return empty
    p = Path(path)
    if not p.is_file():
        die(f"--allowlist file not found: {path}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    managers = raw.get("trusted_manager_ids") or {}
    if isinstance(managers, list):
        managers = {m: "" for m in managers}
    return {
        "managers": {norm_cid(k): str(v) for k, v in managers.items()},
        "domains": [d.lower().lstrip("@") for d in raw.get("trusted_email_domains") or []],
        "emails": {e.lower() for e in raw.get("trusted_emails") or []},
    }


def load_hostile(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        die(f"--hostile-list file not found: {path}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {norm_cid(k): str(v) for k, v in raw.items()}


# --------------------------------------------------------------------------
# Live collection (read-only GoogleAdsService.search only)
# --------------------------------------------------------------------------

def _load_or_die(loader):
    """Build the client; report config/auth failures in one line (no secret values are printed)."""
    try:
        return loader()
    except Exception as exc:  # library raises ValueError / RefreshError on bad config
        first = str(exc).splitlines()[0][:200] if str(exc) else ""
        die(f"could not load Google Ads credentials ({type(exc).__name__}: {first}). "
            "Check google-ads.yaml or GOOGLE_ADS_* variables; login_customer_id must be 10 digits without dashes.")


def build_live_client(config_path: str | None):
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError:
        die("google-ads is not installed. Run: pip install -r requirements.txt")
    if config_path:
        if not os.path.isfile(config_path):
            die(f"credentials file not found: {config_path}")
        client = _load_or_die(lambda: GoogleAdsClient.load_from_storage(config_path, version=GOOGLE_ADS_API_VERSION))
        import yaml
        with open(config_path, encoding="utf-8") as fh:
            login = norm_cid((yaml.safe_load(fh) or {}).get("login_customer_id"))
    else:
        if not any(k.startswith("GOOGLE_ADS_") for k in os.environ):
            die(
                "no credentials: pass --config google-ads.yaml or set GOOGLE_ADS_DEVELOPER_TOKEN, "
                "GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN and "
                "GOOGLE_ADS_LOGIN_CUSTOMER_ID (see SKILL.md)."
            )
        client = _load_or_die(lambda: GoogleAdsClient.load_from_env(version=GOOGLE_ADS_API_VERSION))
        login = norm_cid(os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"))
    return client, login


def _short_error(exc: Exception) -> str:
    try:
        from google.ads.googleads.errors import GoogleAdsException
    except ImportError:  # pragma: no cover
        GoogleAdsException = ()  # type: ignore
    if GoogleAdsException and isinstance(exc, GoogleAdsException):
        codes = []
        for err in exc.failure.errors:
            codes.append(str(err.error_code).strip().replace("\n", " "))
        return ("; ".join(codes) or type(exc).__name__)[:160]
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:120] if str(exc) else ''}"


def collect_live(client, login_cid: str, workers: int) -> dict:
    ga = client.get_service("GoogleAdsService")
    print(f"Walking manager tree from {fmt_cid(login_cid)} (API {GOOGLE_ADS_API_VERSION})...", file=sys.stderr)
    accounts = []
    for row in ga.search(customer_id=login_cid, query=WALK_QUERY):
        cc = row.customer_client
        accounts.append({
            "cid": str(cc.id),
            "name": cc.descriptive_name,
            "status": cc.status.name,
            "level": int(cc.level),
            "is_manager": bool(cc.manager),
        })
    print(f"  {len(accounts)} accounts found", file=sys.stderr)

    def scan_one(cid: str):
        errs = []
        links, users, invites = [], [], []
        try:
            for row in ga.search(customer_id=cid, query=LINK_QUERY):
                ml = row.customer_manager_link
                links.append({
                    "manager_cid": ml.manager_customer.split("/")[-1] if ml.manager_customer else "",
                    "link_id": int(ml.manager_link_id),
                    "status": ml.status.name,
                })
        except Exception as exc:  # isolate per-account failures
            errs.append(f"customer_manager_link: {_short_error(exc)}")
        try:
            for row in ga.search(customer_id=cid, query=USER_QUERY):
                u = row.customer_user_access
                users.append({
                    "user_id": str(u.user_id),
                    "email": u.email_address,
                    "role": u.access_role.name,
                    "created": u.access_creation_date_time,
                    "inviter": u.inviter_user_email_address,
                    "passkey_enabled": bool(u.passkey_enabled),
                })
        except Exception as exc:
            errs.append(f"customer_user_access: {_short_error(exc)}")
        try:
            for row in ga.search(customer_id=cid, query=INVITE_QUERY):
                inv = row.customer_user_access_invitation
                invites.append({
                    "invitation_id": str(inv.invitation_id),
                    "email": inv.email_address,
                    "role": inv.access_role.name,
                    "status": inv.invitation_status.name,
                    "created": inv.creation_date_time,
                })
        except Exception as exc:
            errs.append(f"customer_user_access_invitation: {_short_error(exc)}")
        return cid, links, users, invites, errs

    snap = {
        "login_customer_id": login_cid,
        "as_of": datetime.now(timezone.utc).date().isoformat(),
        "api_version": GOOGLE_ADS_API_VERSION,
        "accounts": accounts,
        "manager_links": {},
        "user_access": {},
        "invitations": {},
        "errors": {},
    }
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(scan_one, a["cid"]) for a in accounts]
        for fut in as_completed(futures):
            cid, links, users, invites, errs = fut.result()
            snap["manager_links"][cid] = links
            snap["user_access"][cid] = users
            snap["invitations"][cid] = invites
            if errs:
                snap["errors"][cid] = " | ".join(errs)
            done += 1
            if done % 250 == 0 or done == len(accounts):
                print(f"  [{done}/{len(accounts)}] scanned, errors: {len(snap['errors'])}", file=sys.stderr)
    return snap


# --------------------------------------------------------------------------
# Analysis (pure; identical for live and fixture input)
# --------------------------------------------------------------------------

def analyze(snap: dict, clients: dict, allow: dict, hostile: dict, as_of: date,
            recent_days: int, max_admins: int) -> dict:
    login = norm_cid(snap["login_customer_id"])
    accounts = {norm_cid(a["cid"]): a for a in snap["accounts"]}
    internal_managers = {c for c, a in accounts.items() if a.get("is_manager")} | {login}
    by_cid = clients["by_cid"]

    def client_label(cid: str) -> dict:
        acct = accounts.get(cid, {})
        mapped = by_cid.get(cid) or []
        if mapped:
            c = mapped[0]
            return {"client_slug": c["slug"], "client_name": c["name"], "client_domain": c["domain"]}
        if cid == login:
            return {"client_slug": None, "client_name": "(agency login MCC)", "client_domain": ""}
        if acct.get("is_manager"):
            return {"client_slug": None, "client_name": "(agency sub-manager)", "client_domain": ""}
        return {"client_slug": None, "client_name": "UNMAPPED", "client_domain": ""}

    def classify_manager(mcid: str) -> str:
        if mcid in hostile:
            return "HOSTILE"
        if mcid in internal_managers:
            return "INTERNAL"
        if mcid in allow["managers"]:
            return "ALLOWLISTED"
        return "EXTERNAL"

    def classify_user(email: str, client_domain: str) -> str:
        e = (email or "").lower()
        d = email_domain(e)
        if e in allow["emails"]:
            return "allowlisted email"
        if any(domain_matches(d, t) for t in allow["domains"]):
            return "allowlisted domain"
        if client_domain and d and domain_matches(d, client_domain):
            return "client domain"
        return ""

    def days_ago(value: str):
        d = parse_day(value)
        return (as_of - d).days if d else None

    findings: list[dict] = []

    def add(sev, kind, cid, title, why, action, **evidence):
        acct = accounts.get(cid, {})
        f = {
            "severity": sev,
            "kind": kind,
            "account_id": fmt_cid(cid) if cid else "",
            "account_name": acct.get("name", ""),
            "account_status": acct.get("status", ""),
            **client_label(cid),
            "title": title,
            "why": why,
            "recommended_action": action,
            "evidence": evidence,
        }
        findings.append(f)

    # ---- Manager links -------------------------------------------------
    link_rows = []
    for cid, links in snap.get("manager_links", {}).items():
        cid = norm_cid(cid)
        for link in links:
            mcid = norm_cid(link["manager_cid"])
            link_rows.append({
                "account_cid": cid,
                "manager_cid": mcid,
                "link_id": int(link["link_id"]),
                "status": link["status"],
                "classification": classify_manager(mcid),
            })
    ext_live_by_mgr = defaultdict(set)
    ext_all_by_mgr = defaultdict(set)
    for r in link_rows:
        if r["classification"] == "EXTERNAL":
            ext_all_by_mgr[r["manager_cid"]].add(r["account_cid"])
            if r["status"] in LIVE_LINK_STATUSES:
                ext_live_by_mgr[r["manager_cid"]].add(r["account_cid"])

    allowlisted_links = []
    for r in link_rows:
        cid, mcid, cls, status = r["account_cid"], r["manager_cid"], r["classification"], r["status"]
        ev = {"manager_id": fmt_cid(mcid), "manager_link_id": r["link_id"], "link_status": status}
        spread = len(ext_all_by_mgr.get(mcid, ()))
        if spread > 1:
            ev["manager_seen_on_accounts"] = spread
        if cls == "HOSTILE":
            ev["hostile_note"] = hostile[mcid]
            if status in LIVE_LINK_STATUSES:
                add("CRITICAL", "hostile_manager_link", cid,
                    f"Hostile manager {fmt_cid(mcid)} is linked (status {status})",
                    "This manager ID is on your hostile list and currently has (or is requesting) access.",
                    "Incident mode: in the Google Ads UI (Admin > Access and security > Managers) decline/remove the link, "
                    "have the client rotate admin credentials, and review change history since the link appeared.",
                    **ev)
            else:
                add("MEDIUM", "hostile_manager_history", cid,
                    f"Hostile manager {fmt_cid(mcid)} appears in link history ({status})",
                    "Not live access, but a convicted manager ID touched this account.",
                    "Confirm the link stays revoked, confirm credential rotation happened, and keep the ID on the hostile list.",
                    **ev)
        elif cls == "EXTERNAL":
            if status == "PENDING":
                add("HIGH", "external_manager_pending", cid,
                    f"Pending link request from unknown manager {fmt_cid(mcid)}",
                    "A live invitation from a manager outside your tree and allowlist is awaiting acceptance. "
                    "Catching it before acceptance is the highest-value outcome of this scan.",
                    "Do not accept. Put a name on the ID via the account's Access and security page, ask the client whether "
                    "they authorized it, and decline it in the UI if nobody can.",
                    **ev)
            elif status == "ACTIVE":
                add("HIGH", "external_manager_active", cid,
                    f"Active link to unknown manager {fmt_cid(mcid)}",
                    "A manager outside your tree and allowlist has live access to this account.",
                    "Identify the manager in the UI and with the client. If expected (former agency, parent company), add it to "
                    "the allowlist with a note; if not, remove it in the UI, rotate client admin credentials and review change history.",
                    **ev)
            elif mcid in ext_live_by_mgr and cid not in ext_live_by_mgr[mcid]:
                add("MEDIUM", "external_manager_probing", cid,
                    f"Unknown manager {fmt_cid(mcid)}: {status} here, live link elsewhere in the book",
                    "Refused or cancelled attempts from a manager that holds or requests access on other accounts in the book "
                    "look like probing across clients.",
                    "Treat together with that manager's live-link findings; investigate as one incident across the affected clients.",
                    live_on_accounts=sorted(fmt_cid(c) for c in ext_live_by_mgr[mcid]), **ev)
            else:
                add("LOW", "external_manager_history", cid,
                    f"Unknown manager {fmt_cid(mcid)} in link history ({status})",
                    "Not live access. Attempt and teardown records still show who has tried to get in.",
                    "Read for patterns; no action unless the same ID appears elsewhere.",
                    **ev)
        elif cls == "ALLOWLISTED" and status in LIVE_LINK_STATUSES:
            allowlisted_links.append({
                "account_id": fmt_cid(cid), **client_label(cid),
                "manager_id": fmt_cid(mcid), "manager_link_id": r["link_id"],
                "link_status": status, "note": allow["managers"][mcid],
            })

    # ---- Users ----------------------------------------------------------
    user_count = 0
    for cid, users in snap.get("user_access", {}).items():
        cid = norm_cid(cid)
        cdomain = client_label(cid)["client_domain"]
        admins = []
        for u in users:
            user_count += 1
            role = u.get("role", "")
            email = u.get("email", "")
            known = classify_user(email, cdomain)
            age = days_ago(u.get("created", ""))
            recent = age is not None and age <= recent_days
            ev = {"user_email": email, "user_id": u.get("user_id", ""), "role": role,
                  "granted": u.get("created", ""), "inviter": u.get("inviter", "")}
            if role == "ADMIN":
                admins.append(email)
            if not known:
                if role == "ADMIN":
                    sev, kind = ("CRITICAL" if recent else "HIGH"), "unknown_admin_user"
                elif role == "STANDARD":
                    sev, kind = ("HIGH" if recent else "MEDIUM"), "unknown_write_user"
                else:
                    sev, kind = ("MEDIUM" if recent else "LOW"), "unknown_readonly_user"
                when = f", granted {age} days ago" if recent else ""
                add(sev, kind, cid,
                    f"Unknown user {email} has {role} access{when}",
                    "Email is not on the allowlist and not on the client's own website domain."
                    + (" Admin access can add managers and users." if role == "ADMIN" else ""),
                    "Confirm with the account lead and client who this is. If unrecognized, remove the user in the UI "
                    "(Admin > Access and security > Users) and ask the inviter how it was granted; if expected, add the "
                    "email or domain to the allowlist.",
                    **ev)
            elif recent:
                add("INFO", "recent_access_change", cid,
                    f"Recent access grant: {email} ({role}), {age} days ago",
                    f"Known user ({known}) but new within the {recent_days}-day window.",
                    "Confirm the grant matches an onboarding or staffing change.",
                    **ev)
            if role == "ADMIN" and known and u.get("passkey_enabled") is False:
                add("LOW", "admin_without_passkey", cid,
                    f"Admin {email} has no passkey enabled",
                    "Admin credentials are the known entry path for manager-link fraud.",
                    "Ask the user to enable a passkey or 2-step verification on their Google account.",
                    **ev)
        if len(admins) > max_admins:
            add("MEDIUM", "too_many_admins", cid,
                f"{len(admins)} admin users (threshold {max_admins})",
                "Every admin can link managers and invite users; more admins means more credentials to compromise.",
                "Review whether each admin needs admin rather than standard access; downgrade in the UI with client approval.",
                admins=sorted(admins))

    invite_count = 0
    for cid, invites in snap.get("invitations", {}).items():
        cid = norm_cid(cid)
        cdomain = client_label(cid)["client_domain"]
        for inv in invites:
            if inv.get("status") != "PENDING":
                continue
            invite_count += 1
            email, role = inv.get("email", ""), inv.get("role", "")
            if classify_user(email, cdomain):
                continue
            ev = {"invite_email": email, "invitation_id": inv.get("invitation_id", ""),
                  "role": role, "created": inv.get("created", "")}
            if role == "ADMIN":
                add("HIGH", "unknown_admin_invitation", cid,
                    f"Pending ADMIN invitation to unknown email {email}",
                    "An unaccepted invitation would grant admin access to an address outside the allowlist and client domain.",
                    "Confirm who sent it; if unrecognized, revoke the invitation in the UI before it is accepted.",
                    **ev)
            else:
                add("MEDIUM" if role == "STANDARD" else "LOW", "unknown_invitation", cid,
                    f"Pending {role} invitation to unknown email {email}",
                    "Invitation to an address outside the allowlist and client domain.",
                    "Confirm it is expected; otherwise revoke it in the UI.",
                    **ev)

    # ---- Client mapping ---------------------------------------------------
    for cid, acct in accounts.items():
        if acct.get("is_manager") or cid == login:
            continue
        mapped = by_cid.get(cid) or []
        if not mapped:
            live = acct.get("status") in ("ENABLED", "SUSPENDED")
            add("MEDIUM" if live else "LOW", "unmapped_account", cid,
                "Account is not mapped to any client profile",
                "No clients/*/client.json lists this customer ID, so nobody owns its access review.",
                "Identify the owner. Add accounts.google_ads_customer_id to the right client.json, or unlink the "
                "account from the agency MCC (with approval) if it no longer belongs in the book.")
        elif len(mapped) > 1:
            add("MEDIUM", "ambiguous_client_mapping", cid,
                "Customer ID is claimed by more than one client profile",
                "Findings are labeled with the first profile by slug; the other claim may be a typo.",
                "Fix accounts.google_ads_customer_id so exactly one client.json lists this ID.",
                profiles=sorted(m["slug"] for m in mapped))
    for cid, mapped in by_cid.items():
        if cid not in accounts:
            for m in mapped:
                f = {
                    "severity": "LOW", "kind": "mapped_account_not_in_tree",
                    "account_id": fmt_cid(cid), "account_name": "", "account_status": "",
                    "client_slug": m["slug"], "client_name": m["name"], "client_domain": m["domain"],
                    "title": "Client profile points at an account outside this manager tree",
                    "why": "This run cannot see the account, so its access was not audited.",
                    "recommended_action": "Check the customer ID in client.json, or run the audit from the manager that owns it.",
                    "evidence": {},
                }
                findings.append(f)

    # ---- Coverage -----------------------------------------------------------
    for cid, msg in snap.get("errors", {}).items():
        cid = norm_cid(cid)
        live = accounts.get(cid, {}).get("status") in ("ENABLED", "SUSPENDED")
        add("MEDIUM" if live else "LOW", "scan_error", cid,
            "Queries failed; this account was not audited",
            "Absence of findings here is not evidence of safety."
            + ("" if live else " Errors are common on cancelled or closed accounts."),
            "Re-run this account alone and read the error; fix credentials or access if it is not a closed account.",
            error=msg)

    def sort_key(f):
        ev = f["evidence"]
        return (
            SEV_RANK[f["severity"]],
            KIND_RANK.get(f["kind"], 99),
            -int(ev.get("manager_link_id", 0) or 0),
            "".join(chr(0x10FFFF - ord(ch)) for ch in str(ev.get("granted") or ev.get("created") or "")),
            f["account_id"],
            f["title"],
        )

    findings.sort(key=sort_key)
    for i, f in enumerate(findings, 1):
        f["id"] = i

    return {
        "login_customer_id": fmt_cid(login),
        "as_of": as_of.isoformat(),
        "recent_days": recent_days,
        "totals": {
            "accounts": len(accounts),
            "internal_managers": len(internal_managers),
            "client_profiles": clients["profiles"],
            "manager_links": len(link_rows),
            "user_grants": user_count,
            "pending_invitations": invite_count,
            "accounts_with_errors": len(snap.get("errors", {})),
        },
        "severity_counts": {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES},
        "findings": findings,
        "allowlisted_links": sorted(allowlisted_links, key=lambda r: (r["account_id"], -r["manager_link_id"])),
        "profiles_without_customer_id": clients["missing_id"],
        "link_rows": sorted(link_rows, key=lambda r: (r["account_cid"], -r["link_id"])),
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def render_markdown(rep: dict) -> str:
    t = rep["totals"]
    out = []
    out.append(f"# MCC security audit: login manager {rep['login_customer_id']}")
    out.append("")
    out.append(f"As of {rep['as_of']}. Read-only scan: nothing was removed, declined or changed. "
               "Internal agency report, not client-facing.")
    out.append("")
    out.append(f"Scanned {t['accounts']} accounts ({t['internal_managers']} agency managers), "
               f"{t['manager_links']} manager links, {t['user_grants']} user grants, "
               f"{t['pending_invitations']} pending invitation(s), against {t['client_profiles']} client profiles. "
               f"{t['accounts_with_errors']} account(s) had query errors. Recent window: {rep['recent_days']} days.")
    out.append("")
    out.append("| Severity | Findings |")
    out.append("|---|---|")
    for s in SEVERITIES:
        out.append(f"| {s} | {rep['severity_counts'][s]} |")
    out.append("")
    out.append("## Findings")
    out.append("")
    if not rep["findings"]:
        out.append("No findings.")
        out.append("")
    for f in rep["findings"]:
        client = f["client_name"] + (f" ({f['client_slug']})" if f["client_slug"] else "")
        acct = f["account_id"]
        if f["account_name"]:
            acct += f" {f['account_name']}"
        if f["account_status"]:
            acct += f" [{f['account_status']}]"
        out.append(f"### {f['id']}. [{f['severity']}] {f['title']}")
        out.append(f"- Client: {client}")
        out.append(f"- Account: {acct}")
        for k in sorted(f["evidence"]):
            v = f["evidence"][k]
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            if v in ("", None):
                continue
            out.append(f"- {k}: {v}")
        out.append(f"- Why: {f['why']}")
        out.append(f"- Recommended action: {f['recommended_action']}")
        out.append("")
    out.append("## Allowlisted managers with live links (re-audit on cadence)")
    out.append("")
    if rep["allowlisted_links"]:
        out.append("| Client | Account | Manager | Link ID | Status | Allowlist note |")
        out.append("|---|---|---|---|---|---|")
        for r in rep["allowlisted_links"]:
            out.append(f"| {r['client_name']} | {r['account_id']} | {r['manager_id']} | "
                       f"{r['manager_link_id']} | {r['link_status']} | {r['note']} |")
    else:
        out.append("None.")
    out.append("")
    if rep["profiles_without_customer_id"]:
        out.append("Client profiles without accounts.google_ads_customer_id (not audited): "
                   + ", ".join(rep["profiles_without_customer_id"]))
        out.append("")
    out.append("Any removal of a link, user or invitation is a manual action in the Google Ads UI and needs "
               "explicit approval naming the client and the change. Client messages drafted from this report are "
               "drafts for the account lead.")
    return "\n".join(out) + "\n"


def json_report(rep: dict) -> dict:
    return {k: v for k, v in rep.items() if k != "link_rows"}


def write_csvs(rep: dict, csv_dir: str):
    os.makedirs(csv_dir, exist_ok=True)
    stamp = rep["as_of"].replace("-", "")
    links_path = os.path.join(csv_dir, f"mcc_link_scan_{stamp}.csv")
    with open(links_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["account_id", "manager_id", "manager_link_id", "link_status", "classification"])
        for r in rep["link_rows"]:
            w.writerow([fmt_cid(r["account_cid"]), fmt_cid(r["manager_cid"]), r["link_id"], r["status"], r["classification"]])
    find_path = os.path.join(csv_dir, f"mcc_security_findings_{stamp}.csv")
    with open(find_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "severity", "kind", "client", "account_id", "account_name", "title", "evidence", "recommended_action"])
        for f in rep["findings"]:
            w.writerow([f["id"], f["severity"], f["kind"], f["client_slug"] or f["client_name"], f["account_id"],
                        f["account_name"], f["title"], json.dumps(f["evidence"], sort_keys=True), f["recommended_action"]])
    print(f"CSV written: {links_path}, {find_path}", file=sys.stderr)


def upload_to_sheet(rep: dict, sheet_id: str):
    """Optional: overwrite two tabs in an agency-owned Google Sheet (gspread service account)."""
    try:
        import gspread
    except ImportError:
        print("WARNING: gspread not installed; skipping Sheets export (pip install gspread).", file=sys.stderr)
        return
    sh = gspread.service_account().open_by_key(sheet_id)
    tabs = {
        "Security Findings": [["id", "severity", "kind", "client", "account_id", "title", "recommended_action"]]
        + [[f["id"], f["severity"], f["kind"], f["client_slug"] or f["client_name"], f["account_id"], f["title"],
            f["recommended_action"]] for f in rep["findings"]],
        "All Manager Links": [["account_id", "manager_id", "manager_link_id", "link_status", "classification"]]
        + [[fmt_cid(r["account_cid"]), fmt_cid(r["manager_cid"]), str(r["link_id"]), r["status"], r["classification"]]
           for r in rep["link_rows"]],
    }
    for title, rows in tabs.items():
        try:
            ws = sh.worksheet(title)
            ws.clear()
        except Exception:
            ws = sh.add_worksheet(title=title, rows=max(len(rows) + 10, 100), cols=len(rows[0]))
        ws.update(range_name="A1", values=[[str(c) for c in r] for r in rows])
    print(f"Sheet updated: https://docs.google.com/spreadsheets/d/{sheet_id}", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read-only security audit of a Google Ads manager (MCC) tree, labeled by client. "
                    f"Uses Google Ads API {GOOGLE_ADS_API_VERSION}. Never removes links, users or invitations.")
    src = ap.add_argument_group("input")
    src.add_argument("--config", help="Path to google-ads.yaml. If omitted, GOOGLE_ADS_* environment variables are used.")
    src.add_argument("--fixture", help="Recorded rows JSON (see DATA_CONTRACT.md). Offline; no credentials needed.")
    src.add_argument("--login-customer-id", help="Override the login manager ID from config/env/fixture.")
    src.add_argument("--clients-dir", default="clients", help="Directory of clients/<slug>/client.json (default: clients)")
    src.add_argument("--allowlist", help="JSON: trusted_manager_ids, trusted_email_domains, trusted_emails")
    src.add_argument("--hostile-list", help="JSON dict of known-hostile manager ID -> note")
    opt = ap.add_argument_group("analysis")
    opt.add_argument("--recent-days", type=int, default=30, help="Window for 'recent' access grants (default: 30)")
    opt.add_argument("--max-admins", type=int, default=3, help="Flag accounts with more admin users than this (default: 3)")
    opt.add_argument("--as-of", help="Reference date YYYY-MM-DD (default: fixture as_of, else today UTC)")
    opt.add_argument("--workers", type=int, default=10, help="Parallel accounts in live mode (default: 10)")
    out = ap.add_argument_group("output")
    out.add_argument("--json", action="store_true", help="Print JSON instead of markdown")
    out.add_argument("--csv-dir", help="Also write datestamped link and findings CSVs here")
    out.add_argument("--sheet-id", help="Also overwrite tabs in this Google Sheet (needs gspread + service account)")
    out.add_argument("--dump-rows", help="Live mode: save the raw collected rows to this JSON (reusable with --fixture). Contains user emails.")
    args = ap.parse_args(argv)

    clients = load_clients(Path(args.clients_dir))
    allow = load_allowlist(args.allowlist)
    hostile = load_hostile(args.hostile_list)

    if args.fixture:
        p = Path(args.fixture)
        if not p.is_file():
            die(f"--fixture file not found: {args.fixture}")
        snap = json.loads(p.read_text(encoding="utf-8"))
        if args.login_customer_id:
            snap["login_customer_id"] = norm_cid(args.login_customer_id)
    else:
        client, login = build_live_client(args.config)
        login = norm_cid(args.login_customer_id) or login
        if not login:
            die("login_customer_id is not set (google-ads.yaml, GOOGLE_ADS_LOGIN_CUSTOMER_ID, or --login-customer-id).")
        snap = collect_live(client, login, args.workers)
        if args.dump_rows:
            Path(args.dump_rows).write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"Raw rows saved: {args.dump_rows}", file=sys.stderr)

    if not norm_cid(snap.get("login_customer_id")):
        die("fixture is missing login_customer_id")
    as_of_str = args.as_of or snap.get("as_of") or datetime.now(timezone.utc).date().isoformat()
    as_of = parse_day(as_of_str)
    if not as_of:
        die(f"invalid --as-of date: {as_of_str}")

    rep = analyze(snap, clients, allow, hostile, as_of, args.recent_days, args.max_admins)

    if args.csv_dir:
        write_csvs(rep, args.csv_dir)
    if args.sheet_id:
        upload_to_sheet(rep, args.sheet_id)

    if args.json:
        sys.stdout.write(json.dumps(json_report(rep), indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_markdown(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
