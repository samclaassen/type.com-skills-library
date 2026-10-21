#!/usr/bin/env python3
"""
Normalize a Shopify Admin GraphQL `discountNodes` response into discounts.json,
the input of scripts/offer_qa.py.

    python3 scripts/normalize_shopify_discounts.py --in page1.json [--in page2.json ...] \
        --out discounts.json [--exported-at 2026-10-06T14:05:00-04:00] [--shop "Store name"]
    python3 scripts/normalize_shopify_discounts.py --print-query

Each --in file is the JSON exactly as the API returned it ({"data": {"discountNodes": ...}}),
or a JSON list of such pages. Field names follow the GraphQL Admin API 2026-10 docs (use 2025-10 or later). Check
them against the API version you query (DATA_CONTRACT.md lists the older field names this
script also accepts). Never calls an API.

Python 3.8+, standard library only. Exit 0 = written, 2 = invalid input (with the reason).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from decimal import Decimal, InvalidOperation

QUERY = """\
query DiscountsForQA($after: String) {
  discountNodes(first: 25, after: $after, query: "status:active,scheduled") {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      discount {
        __typename
        ... on DiscountCodeBasic {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount
          usageLimit appliesOncePerCustomer tags
          codes(first: 5) { nodes { code } }
          codesCount { count }
          combinesWith { ...Combines }
          context { ...Context }
          minimumRequirement { ...Minimum }
          customerGets { value { ...GetsValue } items { ...Items } }
        }
        ... on DiscountAutomaticBasic {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount tags
          combinesWith { ...Combines }
          context { ...Context }
          minimumRequirement { ...Minimum }
          customerGets { value { ...GetsValue } items { ...Items } }
        }
        ... on DiscountCodeFreeShipping {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount
          usageLimit appliesOncePerCustomer tags
          codes(first: 5) { nodes { code } }
          codesCount { count }
          combinesWith { ...Combines }
          context { ...Context }
          minimumRequirement { ...Minimum }
          maximumShippingPrice { amount }
        }
        ... on DiscountAutomaticFreeShipping {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount tags
          combinesWith { ...Combines }
          context { ...Context }
          minimumRequirement { ...Minimum }
          maximumShippingPrice { amount }
        }
        ... on DiscountCodeBxgy {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount
          usageLimit appliesOncePerCustomer usesPerOrderLimit tags
          codes(first: 5) { nodes { code } }
          codesCount { count }
          combinesWith { ...Combines }
          context { ...Context }
          customerBuys { value { ...BuysValue } items { ...Items } }
          customerGets { value { ...GetsValue } items { ...Items } }
        }
        ... on DiscountAutomaticBxgy {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount usesPerOrderLimit tags
          combinesWith { ...Combines }
          context { ...Context }
          customerBuys { value { ...BuysValue } items { ...Items } }
          customerGets { value { ...GetsValue } items { ...Items } }
        }
        ... on DiscountCodeApp {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount
          usageLimit appliesOncePerCustomer tags
          codes(first: 5) { nodes { code } }
          codesCount { count }
          combinesWith { ...Combines }
          context { ...Context }
          appDiscountType { title }
        }
        ... on DiscountAutomaticApp {
          title status startsAt endsAt updatedAt discountClasses asyncUsageCount tags
          combinesWith { ...Combines }
          context { ...Context }
          appDiscountType { title }
        }
      }
    }
  }
}

fragment Combines on DiscountCombinesWith {
  productDiscounts orderDiscounts shippingDiscounts
  productDiscountsWithTagsOnSameCartLine
}

fragment Context on DiscountContext {
  __typename
  ... on DiscountCustomerSegments { segments { name } }
  ... on DiscountCustomers { customers { id } }
  ... on DiscountMarkets { markets(first: 20) { nodes { name } } }
}

fragment Minimum on DiscountMinimumRequirement {
  __typename
  ... on DiscountMinimumSubtotal { greaterThanOrEqualToSubtotal { amount } }
  ... on DiscountMinimumQuantity { greaterThanOrEqualToQuantity }
}

fragment GetsValue on DiscountCustomerGetsValue {
  __typename
  ... on DiscountPercentage { percentage }
  ... on DiscountAmount { amount { amount } appliesOnEachItem }
  ... on DiscountOnQuantity {
    quantity { quantity }
    effect {
      __typename
      ... on DiscountPercentage { percentage }
      ... on DiscountAmount { amount { amount } }
    }
  }
}

fragment BuysValue on DiscountCustomerBuysValue {
  __typename
  ... on DiscountQuantity { quantity }
  ... on DiscountPurchaseAmount { amount }
}

fragment Items on DiscountItems {
  __typename
  ... on DiscountProducts {
    products(first: 50) { pageInfo { hasNextPage } nodes { handle } }
    productVariants(first: 50) { pageInfo { hasNextPage } nodes { sku } }
  }
  ... on DiscountCollections { collections(first: 50) { pageInfo { hasNextPage } nodes { title } } }
}
"""


class InputError(Exception):
    pass


def number(v, where: str):
    """Decimal-safe JSON number: 25 stays 25, '75.0' becomes 75, '7.50' becomes 7.5."""
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise InputError(f"{where}: {v!r} is not a number")
    if not d.is_finite():
        raise InputError(f"{where}: {v!r} is not a number")
    d = d.normalize()
    return int(d) if d == d.to_integral_value() else float(d)


def percent(v, where: str, warnings: list):
    """Shopify percentages are fractions (0.25 = 25%). Values above 1 are taken as already in percent."""
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise InputError(f"{where}: percentage {v!r} is not a number")
    if not d.is_finite():
        raise InputError(f"{where}: percentage {v!r} is not a number")
    if d > 1:
        warnings.append(f"{where}: percentage {v} is above 1; treated as {v}% (Shopify returns 0.0–1.0)")
        return number(d, where)
    return number(d * 100, where)


def nodes(conn) -> list:
    if not conn:
        return []
    if isinstance(conn, list):
        return conn
    if conn.get("nodes") is not None:
        return conn["nodes"]
    return [e.get("node") or {} for e in conn.get("edges") or []]


def truncated(*conns) -> bool:
    """True when any nested connection says it has more than the first page (first: 50)."""
    return any(isinstance(c, dict) and (c.get("pageInfo") or {}).get("hasNextPage") for c in conns)


def items_target(items, where: str, warnings: list | None = None) -> dict:
    out = _items_target(items, where)
    if items and truncated(items.get("collections"), items.get("products"), items.get("productVariants")):
        out["truncated"] = True
        if warnings is not None:
            warnings.append(f"{where}: target truncated at 50; check Applies to in the admin "
                            f"(the analyzer skips its eligibility check)")
    return out


def _items_target(items, where: str) -> dict:
    if not items:
        return {"type": "all"}
    tn = items.get("__typename")
    if tn == "AllDiscountItems" or items.get("allItems"):
        return {"type": "all"}
    if tn == "DiscountCollections" or "collections" in items:
        return {"type": "collections", "collections": [c.get("title") or c.get("handle") or ""
                                                       for c in nodes(items.get("collections"))]}
    if tn == "DiscountProducts" or "products" in items or "productVariants" in items:
        return {"type": "products", "products": [p.get("handle") or "" for p in nodes(items.get("products"))],
                "variants": [v.get("sku") or "" for v in nodes(items.get("productVariants")) if v.get("sku")]}
    raise InputError(f"{where}: unknown items type {tn!r} (add __typename to the Items fragment)")


def eligibility(disc: dict) -> dict:
    ctx = disc.get("context")
    if ctx is None:
        ctx = disc.get("customerSelection")          # deprecated name in older API versions
    if not ctx:
        return {"type": "all"}
    tn = ctx.get("__typename", "")
    if tn in ("DiscountBuyerSelectionAll", "DiscountCustomerAll") or ctx.get("all") or ctx.get("allCustomers"):
        return {"type": "all"}
    if tn == "DiscountCustomerSegments" or "segments" in ctx:
        return {"type": "segments", "names": sorted(s.get("name") or s.get("id") or "" for s in ctx.get("segments") or [])}
    if tn == "DiscountCustomers" or "customers" in ctx:
        return {"type": "customers", "names": [], "count": len(ctx.get("customers") or [])}
    if tn == "DiscountMarkets" or "markets" in ctx:
        return {"type": "markets", "names": sorted(m.get("name") or "" for m in nodes(ctx.get("markets")))}
    return {"type": "unknown", "names": []}


def minimum(req, where: str):
    if not req:
        return None
    tn = req.get("__typename", "")
    if tn == "DiscountMinimumSubtotal" or "greaterThanOrEqualToSubtotal" in req:
        amt = req.get("greaterThanOrEqualToSubtotal") or {}
        return {"subtotal": number(amt.get("amount") if isinstance(amt, dict) else amt, where + " minimum")}
    if tn == "DiscountMinimumQuantity" or "greaterThanOrEqualToQuantity" in req:
        return {"quantity": number(req.get("greaterThanOrEqualToQuantity"), where + " minimum")}
    raise InputError(f"{where}: unknown minimumRequirement {tn!r}")


def classes_of(disc: dict, kind: str, where: str) -> list:
    cl = disc.get("discountClasses")
    if not cl and disc.get("discountClass"):
        cl = [disc["discountClass"]]                 # deprecated single-class field
    if not cl and disc.get("appDiscountType"):
        cl = disc["appDiscountType"].get("discountClasses") or (
            [disc["appDiscountType"]["discountClass"]] if disc["appDiscountType"].get("discountClass") else [])
    if not cl:
        if kind == "FreeShipping":
            cl = ["SHIPPING"]
        elif kind == "Bxgy":
            cl = ["PRODUCT"]
        else:
            raise InputError(f"{where}: no discountClasses in the response (add discountClasses to the query)")
    return [c.lower() for c in cl]


def normalize_node(node: dict, i: int, warnings: list) -> dict:
    disc = node.get("discount") or {}
    tn = disc.get("__typename")
    where = f"node {i} ({node.get('id') or disc.get('title') or '?'})"
    if not tn:
        raise InputError(f"{where}: discount has no __typename (add __typename to the query)")
    if tn.startswith("DiscountAutomatic"):
        method, kind = "automatic", tn[len("DiscountAutomatic"):]
    elif tn.startswith("DiscountCode"):
        method, kind = "code", tn[len("DiscountCode"):]
    else:
        raise InputError(f"{where}: unknown discount type {tn}")
    for f in ("title", "status", "startsAt", "combinesWith"):
        if disc.get(f) is None:
            raise InputError(f"{where}: '{f}' is missing from the response (it is in the query in DATA_CONTRACT.md)")
    classes = classes_of(disc, kind, where)
    cls = classes[0] if len(classes) == 1 else "multi"
    out = {"id": node.get("id") or "", "title": disc["title"], "method": method}
    codes = [c.get("code") for c in nodes(disc.get("codes")) if c.get("code")]
    out["codes"] = codes
    total = (disc.get("codesCount") or {}).get("count")
    if total is not None and total > len(codes):
        out["codesCount"] = total
    if method == "code" and not codes:
        raise InputError(f"{where}: code discount without codes (add codes(first: 5) {{ nodes {{ code }} }})")
    out["class"] = cls
    if cls == "multi":
        out["classes"] = classes
    target = {"type": "all"}
    bxgy = None
    value = None
    if kind == "Basic":
        gets = disc.get("customerGets") or {}
        val = gets.get("value") or {}
        vt = val.get("__typename") or ("DiscountPercentage" if "percentage" in val else "DiscountAmount"
                                         if "amount" in val else "")
        if vt == "DiscountPercentage":
            typ, value = "percentage", percent(val.get("percentage"), where, warnings)
        elif vt == "DiscountAmount":
            typ = "fixed_amount"
            amt = val.get("amount")
            value = number(amt.get("amount") if isinstance(amt, dict) else amt, where + " amount")
        else:
            raise InputError(f"{where}: unknown customerGets.value {vt!r}")
        target = items_target(gets.get("items"), where, warnings) if cls == "product" else \
            ({"type": "unknown"} if cls == "multi" else {"type": "all"})
    elif kind == "Bxgy":
        typ = "bxgy"
        gets = disc.get("customerGets") or {}
        buys = disc.get("customerBuys") or {}
        gv = gets.get("value") or {}
        eff = gv.get("effect") or {}
        pctv = percent(eff.get("percentage"), where, warnings) if "percentage" in eff else None
        amt = eff.get("amount")
        amtv = number(amt.get("amount") if isinstance(amt, dict) else amt, where + " get amount") \
            if "amount" in eff and amt is not None else None
        bv = buys.get("value") or {}
        bq = bv.get("quantity")
        bxgy = {"buys": {"quantity": number(bq, where)} if bq is not None
                else {"amount": number(bv.get("amount"), where)},
                "buysTarget": items_target(buys.get("items"), where, warnings),
                "gets": dict({"quantity": number((gv.get("quantity") or {}).get("quantity"), where), "percentage": pctv},
                             **({"amount": amtv} if amtv is not None else {})),
                "usesPerOrderLimit": disc.get("usesPerOrderLimit")}
        value = pctv
        target = items_target(gets.get("items"), where, warnings)
    elif kind == "FreeShipping":
        typ = "free_shipping"
    elif kind == "App":
        typ = "app"
        target = {"type": "unknown"}           # app logic decides what it reaches; the API doesn't expose it
    else:
        typ = "unknown"
        target = {"type": "unknown"}
    if cls == "multi":
        target = {"type": "unknown"}
    out["type"] = typ
    out["value"] = value
    if typ == "fixed_amount":
        out["appliesOnEachItem"] = bool((disc.get("customerGets") or {}).get("value", {}).get("appliesOnEachItem", True))
    out["target"] = target
    out["minimum"] = minimum(disc.get("minimumRequirement"), where)
    out["eligibility"] = eligibility(disc)
    out["usageLimit"] = disc.get("usageLimit")
    out["oncePerCustomer"] = bool(disc.get("appliesOncePerCustomer", False))
    used = disc.get("asyncUsageCount")
    out["timesUsed"] = used if used is not None else disc.get("usageCount", 0)
    cw = disc["combinesWith"]
    comb = {"productDiscounts": bool(cw.get("productDiscounts")), "orderDiscounts": bool(cw.get("orderDiscounts")),
            "shippingDiscounts": bool(cw.get("shippingDiscounts"))}
    if cw.get("productDiscountsWithTagsOnSameCartLine"):
        comb["sameLineTags"] = list(cw["productDiscountsWithTagsOnSameCartLine"])
    out["combinesWith"] = comb
    out["tags"] = list(disc.get("tags") or [])
    out["startsAt"] = disc["startsAt"]
    out["endsAt"] = disc.get("endsAt")
    out["status"] = disc["status"]
    out["updatedAt"] = disc.get("updatedAt")
    if bxgy is not None:
        out["bxgy"] = bxgy
    if typ == "free_shipping":
        msp = disc.get("maximumShippingPrice")
        out["maxShippingPrice"] = number(msp.get("amount"), where) if isinstance(msp, dict) else number(msp, where)
    if typ == "app":
        out["appTitle"] = (disc.get("appDiscountType") or {}).get("title")
    if typ in ("app", "unknown") and disc.get("summary"):
        out["summary"] = disc["summary"]
    return out


def load_pages(paths: list) -> list:
    pages = []
    for p in paths:
        try:
            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
        except OSError as e:
            raise InputError(f"cannot read {p}: {e.strerror or e}")
        except json.JSONDecodeError as e:
            raise InputError(f"{p} is not valid JSON: {e.msg} at line {e.lineno}")
        if isinstance(data, dict) and isinstance(data.get("pages"), list):
            data = data["pages"]
        for page in data if isinstance(data, list) else [data]:
            if not isinstance(page, dict):
                raise InputError(f"{p}: each page must be a JSON object")
            if page.get("errors"):
                msgs = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in page["errors"])
                raise InputError(f"{p}: the API returned errors: {msgs}")
            conn = ((page.get("data") or {}).get("discountNodes"))
            if conn is None:
                raise InputError(f"{p}: no data.discountNodes in the response")
            pages.append((p, conn))
    if not pages:
        raise InputError("no input pages")
    return pages


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--in", dest="inputs", action="append", default=[], help="GraphQL response JSON (repeat per page)")
    ap.add_argument("--out", help="write discounts.json here (default: stdout)")
    ap.add_argument("--exported-at", help="when the export was taken, ISO with offset (default: now, UTC)")
    ap.add_argument("--shop", default="", help="store name to record in the file")
    ap.add_argument("--allow-partial", action="store_true", help="accept a last page with hasNextPage true")
    ap.add_argument("--print-query", action="store_true", help="print the GraphQL query and exit")
    args = ap.parse_args(argv)
    if args.print_query:
        sys.stdout.write(QUERY)
        return 0
    warnings = []
    try:
        if not args.inputs:
            raise InputError("pass at least one --in file (or --print-query)")
        pages = load_pages(args.inputs)
        last = pages[-1][1].get("pageInfo") or {}
        if last.get("hasNextPage") and not args.allow_partial:
            raise InputError(f"the last page says hasNextPage is true: fetch the next page with "
                             f"after: \"{last.get('endCursor')}\" and pass every page with --in")
        out, seen = [], set()
        for path, conn in pages:
            for node in nodes(conn):
                if node.get("id") and node["id"] in seen:
                    continue
                seen.add(node.get("id"))
                out.append(normalize_node(node, len(out), warnings))
        exported = args.exported_at or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    doc = {"source": "Shopify Admin GraphQL discountNodes", "shop": args.shop, "exportedAt": exported,
           "discounts": out}
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {len(out)} discounts to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
