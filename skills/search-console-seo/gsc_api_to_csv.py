#!/usr/bin/env python3
"""Convert Search Console searchAnalytics.query JSON responses into contract CSV.

Stdlib only, offline, deterministic.

Pass every page of one request (startRow 0, 25000, ...) in order. Each response
looks like {"rows": [{"keys": ["<query>", "<page>"], "clicks": 12.0,
"impressions": 340.0, "ctr": 0.035, "position": 4.2}, ...]}; a response with no
"rows" key is an empty page. Output columns are the requested dimensions
followed by clicks,impressions,ctr,position, sorted for stable diffs.

Examples:
  python3 gsc_api_to_csv.py --dimensions query,page \
      --out current.csv resp_current_0.json resp_current_25000.json
  python3 gsc_api_to_csv.py --dimensions "" --set period=current \
      --out totals.csv resp_totals_current.json
  python3 gsc_api_to_csv.py --dimensions "" --set period=prior \
      --out totals.csv --append resp_totals_prior.json

Exit codes: 0 ok, 2 bad input (message names the file, row and field).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

VALID_DIMS = {"query", "page", "device", "country", "date", "searchAppearance"}


class InputError(Exception):
    pass


def num(row: dict, field: str, where: str) -> float:
    if field not in row:
        raise InputError(f"{where}: field '{field}' is missing")
    v = row[field]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise InputError(f"{where}: field '{field}' is not a number")
    return float(v)


def convert(files: list[str], dims: list[str], sets: list[tuple[str, str]]) -> str:
    out_rows = []
    seen: dict[tuple, str] = {}
    for f in files:
        path = Path(f)
        if not path.is_file():
            raise InputError(f"{f}: file not found")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InputError(f"{f}: invalid JSON at line {exc.lineno}: {exc.msg}")
        if not isinstance(data, dict):
            raise InputError(f"{f}: top level must be a JSON object (one API response)")
        if "error" in data:
            raise InputError(f"{f}: response is an API error: {json.dumps(data['error'])[:200]}")
        rows = data.get("rows", [])
        if not isinstance(rows, list):
            raise InputError(f"{f}: 'rows' must be a list")
        for i, row in enumerate(rows):
            where = f"{f}: rows[{i}]"
            keys = row.get("keys", [])
            if not isinstance(keys, list) or len(keys) != len(dims):
                raise InputError(f"{where}: field 'keys' has {len(keys) if isinstance(keys, list) else 'no'} "
                                 f"values but --dimensions has {len(dims)}")
            clicks = num(row, "clicks", where)
            impr = num(row, "impressions", where)
            ctr = num(row, "ctr", where)
            pos = num(row, "position", where)
            k = tuple(str(x) for x in keys)
            if k in seen:
                raise InputError(f"{where}: duplicate keys {list(k)} (also in {seen[k]}); "
                                 "pages overlap or the same file was passed twice")
            seen[k] = where
            out_rows.append(list(k) + [f"{int(round(clicks))}", f"{int(round(impr))}",
                                       f"{ctr:.6f}", f"{pos:.4f}"])
    out_rows.sort(key=lambda r: r[: len(dims)])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([k for k, _ in sets] + dims + ["clicks", "impressions", "ctr", "position"])
    for r in out_rows:
        w.writerow([v for _, v in sets] + r)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Search Analytics API JSON -> contract CSV")
    ap.add_argument("files", nargs="+", help="response JSON files for ONE request, all pages")
    ap.add_argument("--dimensions", required=True,
                    help="comma list in request order, e.g. query,page; empty string for totals")
    ap.add_argument("--set", action="append", default=[], metavar="COL=VALUE",
                    help="prepend a constant column, e.g. period=current")
    ap.add_argument("--out", help="write CSV here instead of stdout")
    ap.add_argument("--append", action="store_true",
                    help="with --out: append data rows to an existing file with the same header")
    args = ap.parse_args(argv)
    try:
        dims = [d.strip() for d in args.dimensions.split(",") if d.strip()]
        for d in dims:
            if d not in VALID_DIMS:
                raise InputError(f"--dimensions: '{d}' is not a Search Analytics dimension")
        sets = []
        for s in args.set:
            if "=" not in s or not s.split("=", 1)[0]:
                raise InputError(f"--set: '{s}' must look like COL=VALUE")
            sets.append(tuple(s.split("=", 1)))
        text = convert(args.files, dims, sets)
    except InputError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    if args.out:
        out = Path(args.out)
        if args.append and out.is_file():
            existing = out.read_text(encoding="utf-8")
            header, _, body = text.partition("\n")
            if existing.partition("\n")[0] != header:
                sys.stderr.write(f"error: --append: {out} has a different header than this output\n")
                return 2
            out.write_text(existing + body, encoding="utf-8")
        else:
            out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
