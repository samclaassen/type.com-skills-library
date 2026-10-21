#!/usr/bin/env python3
"""GEO / AI search readiness audit for one client website.

Usage:
  geo_audit.py --client <slug-or-name> [--clients-dir clients] [--competitors]
  geo_audit.py --url https://example.com
  geo_audit.py --url https://example.com --html-file page.html [--robots-file robots.txt] [--llms-file llms.txt]
  geo_audit.py --client <slug> --offline-root examples/sites   (serve saved files instead of the network)

Options: --json, --out FILE, --max-pages N, --max-competitors N, --date YYYY-MM-DD, --timeout S.
Output is a DRAFT report for account-lead review; nothing is sent anywhere.
Python 3.9+ standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from geo_lib.audit import audit_site, normalize_url  # noqa: E402
from geo_lib.fetcher import FixtureFetcher, LiveFetcher  # noqa: E402
from geo_lib.report import render_json, render_markdown  # noqa: E402


class ClientError(Exception):
    pass


def _slugify(value: str) -> str:
    value = value.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def load_client(ref: str, clients_dir: Path) -> dict:
    if not clients_dir.is_dir():
        raise ClientError(
            f"No clients directory at {clients_dir}. Create {clients_dir}/<client-slug>/client.json "
            "(see CLIENTS.md) or pass --clients-dir."
        )
    wanted = _slugify(ref)
    for path in sorted(clients_dir.glob("*/client.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            if path.parent.name == wanted:
                raise ClientError(f"{path} is not valid JSON ({exc}). Fix the file and rerun.")
            continue
        if path.parent.name == wanted or _slugify(str(data.get("name", ""))) == wanted:
            data["_slug"] = path.parent.name
            data["_path"] = str(path)
            return data
    raise ClientError(
        f"No client profile matching '{ref}' in {clients_dir}. Add {clients_dir}/{wanted}/client.json "
        "with at least {\"name\": ..., \"website\": ...} (see CLIENTS.md)."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GEO / AI search readiness audit (DRAFT output).")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--client", help="client slug or name from clients/<slug>/client.json")
    target.add_argument("--url", help="audit a single URL without a client profile")
    parser.add_argument("--clients-dir", default="clients", help="directory holding client profiles (default: ./clients)")
    parser.add_argument("--competitors", action="store_true", help="benchmark against the client's listed competitors")
    parser.add_argument("--max-competitors", type=int, default=3)
    parser.add_argument("--max-pages", type=int, default=5, help="pages per site, homepage first (default 5)")
    parser.add_argument("--offline-root", help="serve <root>/<host>/... files instead of fetching (offline mode)")
    parser.add_argument("--html-file", help="saved HTML for --url (offline; one page only)")
    parser.add_argument("--robots-file", help="saved robots.txt to use with --html-file")
    parser.add_argument("--llms-file", help="saved llms.txt to use with --html-file")
    parser.add_argument("--date", help="audit date to print (default: today)")
    parser.add_argument("--timeout", type=int, default=20, help="per-request timeout in live mode (seconds)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of Markdown")
    parser.add_argument("--out", help="write the report to this file instead of stdout")
    args = parser.parse_args(argv)

    try:
        client: dict = {}
        competitor_urls: list[str] = []
        if args.client:
            client = load_client(args.client, Path(args.clients_dir))
            if not client.get("website"):
                raise ClientError(
                    f"Client '{client.get('name', client['_slug'])}' has no \"website\" field in {client['_path']}. "
                    "Add \"website\": \"https://...\" to that file and rerun."
                )
            url = client["website"]
            if args.competitors:
                competitor_urls = [c for c in client.get("competitors") or [] if isinstance(c, str) and c.strip()]
                if not competitor_urls:
                    raise ClientError(
                        f"--competitors was requested but {client['_path']} has no \"competitors\" list. "
                        "Add \"competitors\": [\"https://competitor-a.com\"] or rerun without --competitors."
                    )
        else:
            url = args.url
            if args.competitors:
                raise ClientError("--competitors needs --client (competitors come from the client profile).")
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    max_pages = args.max_pages
    if args.html_file:
        if not args.url:
            print("error: --html-file needs --url (the page's real address).", file=sys.stderr)
            return 2
        home = normalize_url(args.url)
        root = home.split("/", 3)
        base = f"{root[0]}//{root[2]}"
        overrides = {home: Path(args.html_file)}
        not_checked = {"/llms-full.txt"}
        if args.robots_file:
            overrides[f"{base}/robots.txt"] = Path(args.robots_file)
        else:
            not_checked.add("/robots.txt")
        if args.llms_file:
            overrides[f"{base}/llms.txt"] = Path(args.llms_file)
        else:
            not_checked.add("/llms.txt")
        fetcher = FixtureFetcher(None, overrides, not_checked)
        max_pages = 1
    elif args.offline_root:
        fetcher = FixtureFetcher(Path(args.offline_root))
    else:
        fetcher = LiveFetcher(timeout=args.timeout)

    audit_date = args.date or dt.date.today().isoformat()
    main_result = audit_site(url, fetcher, max_pages=max_pages)
    competitor_results = [
        audit_site(c, fetcher, max_pages=max_pages) for c in competitor_urls[: args.max_competitors]
    ]
    render = render_json if args.json else render_markdown
    text = render(client, main_result, competitor_results, audit_date)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"Wrote DRAFT report to {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
