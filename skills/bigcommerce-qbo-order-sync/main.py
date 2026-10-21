"""Credential-free BigCommerce -> QuickBooks import-file planner.

Python 3.11+, standard library only. Reads one JSON document on stdin and writes
one JSON document on stdout. It performs no network, file or credential access.
The output describes rows for human-reviewed QuickBooks imports/worksheets; it
never describes QuickBooks API writes.
"""
import hashlib
import json
import re
import sys
import unicodedata
from decimal import Decimal

VERSION = "3.1.1"
MAX_ORDERS = 400  # 100 listed + up to 100 re-export IDs + up to 200 carried pending (unacknowledged) orders.
MONEY = re.compile(r"^\d{1,10}(?:\.\d{1,6})?$")
EMAIL = re.compile(r"^[^\s@<>]{1,64}@[^\s@<>]{1,190}$")
STORE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,5}$")
CURRENCIES = ["USD", "CAD", "GBP", "EUR", "AUD", "NZD"]
NAME_SOURCES = ["billing_name", "company_then_name", "email"]
PAID_TREATMENTS = ["sales_receipt_worksheet", "invoice_then_payment"]
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
MAX_DOC_NUMBER = 21  # QuickBooks Online DocNumber length limit.


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    if not MONEY.fullmatch(text):
        return None
    number = Decimal(text) * 100
    # Two-decimal currencies only; never silently round source money.
    return int(number) if number == number.to_integral_value() else None


def valid_text(value, max_length):
    return (isinstance(value, str) and len(value) <= max_length
            and not any(unicodedata.category(ch).startswith("C") for ch in value))


def identity_text(value, max_length=100):
    """Names QuickBooks matches exactly. They are never rewritten, so unsafe ones are rejected."""
    return (valid_text(value, max_length) and value != "" and value == value.strip()
            and not value.startswith(FORMULA_START))


def free_text(value, max_length=4000):
    """Descriptive text. The CSV renderer neutralizes formulas and control characters."""
    return value[:max_length] if isinstance(value, str) else ""


def review(order_id, reason):
    return {"orderId": order_id if isinstance(order_id, (str, int)) else None,
            "status": "review", "reason": reason}


def normalize_bc(raw):
    if not isinstance(raw, dict):
        return review(None, "invalid_order")
    source_id = raw.get("id")
    valid_id = ((type(source_id) is int and 0 < source_id <= 10**15)
                or (isinstance(source_id, str) and re.fullmatch(r"[1-9][0-9]{0,14}", source_id)))
    oid = str(source_id) if valid_id else ""
    if not valid_id:
        return review(None, "invalid_order_id")
    products = raw.get("products")
    if not isinstance(products, list) or not products or len(products) > 500:
        return review(oid, "complete_products_required")
    fields = ["total_inc_tax", "total_ex_tax", "subtotal_ex_tax", "subtotal_tax",
              "shipping_cost_ex_tax", "shipping_cost_tax", "discount_amount", "coupon_discount",
              "handling_cost_ex_tax", "handling_cost_tax", "wrapping_cost_ex_tax", "wrapping_cost_tax",
              "refunded_amount", "gift_certificate_amount", "store_credit_amount"]
    money = {k: amount(raw.get(k)) for k in fields}
    if any(v is None for v in money.values()):
        return review(oid, "missing_or_non_cent_order_amount")
    if any(money[k] for k in ["handling_cost_ex_tax", "handling_cost_tax", "wrapping_cost_ex_tax",
                             "wrapping_cost_tax", "gift_certificate_amount", "store_credit_amount"]):
        return review(oid, "unsupported_fees_or_tender")
    if money["refunded_amount"] or raw.get("status_id") in [4, 5, 6, 14]:
        return review(oid, "refund_or_cancellation_requires_reconciliation")
    if type(raw.get("status_id")) is not int:
        return review(oid, "integer_status_id_required")
    billing = raw.get("billing_address")
    if not isinstance(billing, dict) or not valid_text(billing.get("email"), 255) or not EMAIL.fullmatch(billing["email"].strip()):
        return review(oid, "valid_buyer_email_required")
    lines = []
    for p in products:
        if not isinstance(p, dict):
            return review(oid, "invalid_product")
        sku, qty = p.get("sku"), p.get("quantity")
        if (p.get("is_refunded", False) is not False or p.get("quantity_refunded", 0) != 0
            or ("refund_amount" in p and amount(p["refund_amount"]) != 0)):
            return review(oid, "product_refund_requires_reconciliation")
        if not valid_text(sku, 100) or not sku:
            return review(oid, "valid_sku_required")
        if type(qty) is not int or not 1 <= qty <= 100000:
            return review(oid, "positive_integer_quantity_required")
        price, total, tax = (amount(p.get(k)) for k in ["price_ex_tax", "total_ex_tax", "total_tax"])
        if None in [price, total, tax] or price * qty != total:
            return review(oid, "line_price_discount_or_rounding_requires_review")
        if "discounted_total_inc_tax" in p and amount(p["discounted_total_inc_tax"]) != total + tax:
            return review(oid, "discounted_product_total_requires_review")
        # Coupon allocations can already be reflected in product totals. Do not infer.
        discounts = p.get("applied_discounts", [])
        if not isinstance(discounts, list) or discounts:
            return review(oid, "allocated_product_discounts_require_review")
        tax_class = p.get("tax_class_id")
        if type(tax_class) is not int or tax_class < 0:
            return review(oid, "tax_class_required")
        lines.append({"sku": sku, "name": free_text(p.get("name")), "qty": qty, "price": price,
                      "total": total, "tax": tax})
    subtotal = sum(p["total"] for p in lines)
    tax = sum(p["tax"] for p in lines) + money["shipping_cost_tax"]
    discount = money["discount_amount"] + money["coupon_discount"]
    if sum(p["tax"] for p in lines) != money["subtotal_tax"]:
        return review(oid, "line_tax_does_not_reconcile")
    if subtotal != money["subtotal_ex_tax"] or subtotal + money["shipping_cost_ex_tax"] - discount != money["total_ex_tax"]:
        return review(oid, "discount_or_subtotal_does_not_reconcile")
    if money["total_ex_tax"] + tax != money["total_inc_tax"] or money["total_inc_tax"] <= 0:
        return review(oid, "order_total_does_not_reconcile")
    date = raw.get("qbo_txn_date")
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return review(oid, "store_timezone_transaction_date_required")
    # Validate calendar dates without using exception-based normal control flow.
    y, m, d = map(int, date.split("-"))
    days = [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if not 1900 <= y <= 9999 or not 1 <= m <= 12 or not 1 <= d <= days[m-1]:
        return review(oid, "invalid_transaction_date")
    currency = raw.get("default_currency_code")
    if not isinstance(currency, str) or not re.fullmatch("[A-Z]{3}", currency):
        return review(oid, "transactional_currency_required")
    return {"orderId": oid, "lines": lines, "currency": currency, "billing": billing,
            "email": billing["email"].strip().lower(), "date": date, "tax": tax,
            "discount": discount, "shipping": money["shipping_cost_ex_tax"],
            "shippingTax": money["shipping_cost_tax"], "total": money["total_inc_tax"],
            "statusId": raw.get("status_id"), "paymentStatus": raw.get("payment_status")}


def derived_customer_name(n, source):
    billing = n["billing"]
    def text(key):
        value = billing.get(key)
        return " ".join(value.split()) if isinstance(value, str) else ""
    person = " ".join(part for part in [text("first_name"), text("last_name")] if part)
    company = text("company")
    if source == "email":
        return n["email"]
    if source == "company_then_name":
        return company or person or n["email"]
    return person or company or n["email"]


def billing_details(n):
    billing = n["billing"]
    def text(key):
        value = billing.get(key)
        return " ".join(value.split()) if isinstance(value, str) else ""
    street = ", ".join(part for part in [text("street_1"), text("street_2")] if part)
    return {"company": text("company"), "email": n["email"], "phone": text("phone"), "street": street,
            "city": text("city"), "state": text("state"), "zip": text("zip"), "country": text("country_iso2")}


def email_of(record):
    address = record.get("PrimaryEmailAddr")
    if isinstance(address, dict) and isinstance(address.get("Address"), str):
        return address["Address"].strip().lower()
    return None


def resolve_customer(n, config, customers):
    """customers is None (no QuickBooks connection) or a complete list of QBO Customer records."""
    derived = derived_customer_name(n, config["customerNameSource"])
    if customers is None:
        if not identity_text(derived):
            return None, "customer_name_requires_review"
        return {"name": derived, "match": "unverified", "details": billing_details(n)}, None
    matches = [c for c in customers if email_of(c) == n["email"]]
    if len(matches) > 1:
        return None, "ambiguous_customer_email"
    if matches:
        customer = matches[0]
        name = customer.get("DisplayName")
        if customer.get("Active") is False:
            return None, "inactive_customer"
        if not identity_text(name, 500):
            return None, "customer_name_requires_review"
        currency = customer.get("CurrencyRef")
        if isinstance(currency, dict) and currency.get("value") not in (None, n["currency"]):
            return None, "customer_currency_mismatch"
        same_name = [c for c in customers if isinstance(c.get("DisplayName"), str)
                     and c["DisplayName"].casefold() == name.casefold()]
        if len(same_name) != 1:
            return None, "ambiguous_customer_name"
        return {"name": name, "match": "matched", "qboId": str(customer.get("Id", ""))}, None
    if not config["allowNewCustomers"]:
        return None, "customer_not_found"
    if not identity_text(derived):
        return None, "customer_name_requires_review"
    if any(isinstance(c.get("DisplayName"), str) and c["DisplayName"].casefold() == derived.casefold() for c in customers):
        return None, "customer_name_collision"
    return {"name": derived, "match": "new", "details": billing_details(n)}, None


def vendor_status(name, vendors):
    if vendors is None:
        return "unverified"
    found = [v for v in vendors if isinstance(v.get("DisplayName"), str)
             and v["DisplayName"].casefold() == name.casefold() and v.get("Active") is not False]
    return "matched" if len(found) == 1 else "not_found_in_qbo" if not found else "ambiguous_in_qbo"


def reconciles(lines, total):
    return sum(line["amountCents"] for line in lines) == total


def plan_order(raw, config, customers, customer_state, vendors):
    n = normalize_bc(raw)
    if n.get("status") == "review":
        return n
    oid = n["orderId"]
    if n["tax"] != 0:
        return review(oid, "taxable_order_requires_manual_entry")
    if n["statusId"] not in config["eligibleStatusIds"]:
        return review(oid, "ineligible_order_status")
    if n["currency"] != config["currency"]:
        return review(oid, "currency_not_approved")
    if n["paymentStatus"] == "captured":
        kind = "sales_receipt" if config["paidOrderTreatment"] == "sales_receipt_worksheet" else "invoice_then_payment"
    elif n["paymentStatus"] in ["pending", "authorized"]:
        kind = "invoice"
    else:
        return review(oid, "unsupported_payment_status")
    # QuickBooks invoice import rejects negative charges such as discounts.
    if kind != "sales_receipt" and n["discount"]:
        return review(oid, "discount_not_supported_by_invoice_import")
    if customer_state == "incomplete":
        return review(oid, "qbo_customer_list_incomplete")
    customer, problem = resolve_customer(n, config, customers if customer_state == "complete" else None)
    if problem:
        return review(oid, problem)
    lines = []
    for line in n["lines"]:
        item = config["itemMap"].get(line["sku"])
        if not identity_text(item):
            return review(oid, "item_mapping_required")
        lines.append({"type": "product", "sku": line["sku"], "item": item, "description": line["name"],
                      "qty": line["qty"], "rateCents": line["price"], "amountCents": line["total"]})
    if n["shipping"]:
        item = config.get("shippingItemName")
        if not identity_text(item):
            return review(oid, "shipping_item_mapping_required")
        lines.append({"type": "shipping", "sku": "", "item": item, "description": "Shipping",
                      "qty": 1, "rateCents": n["shipping"], "amountCents": n["shipping"]})
    if n["discount"]:
        lines.append({"type": "discount", "sku": "", "item": "", "description": "Order discount",
                      "qty": 1, "rateCents": -n["discount"], "amountCents": -n["discount"]})
    if not reconciles(lines, n["total"]):
        return review(oid, "export_total_does_not_reconcile")
    po_lines = []
    if config["purchaseOrders"]:
        for line in n["lines"]:
            if line["sku"] not in config["suppliers"]:
                continue
            supplier = config["suppliers"][line["sku"]]
            if (not isinstance(supplier, dict) or not identity_text(supplier.get("vendorName"))
                or type(supplier.get("unitCostCents")) is not int or not 0 < supplier["unitCostCents"] <= 10**10
                or supplier.get("currency") != n["currency"] or supplier.get("verified") is not True):
                return review(oid, "supplier_cost_or_currency_requires_review")
            po_lines.append({"vendor": supplier["vendorName"], "vendorStatus": vendor_status(supplier["vendorName"], vendors),
                             "sku": line["sku"], "item": config["itemMap"][line["sku"]], "qty": line["qty"],
                             "unitCostCents": supplier["unitCostCents"],
                             "amountCents": supplier["unitCostCents"] * line["qty"]})
    doc_number = config["invoicePrefix"] + oid
    if len(doc_number) > MAX_DOC_NUMBER:
        return review(oid, "document_number_too_long")
    return {"orderId": oid, "status": "planned", "kind": kind, "docNumber": doc_number, "date": n["date"],
            "currency": n["currency"], "totalCents": n["total"], "customer": customer, "lines": lines,
            "purchaseLines": po_lines}


def config_problem(c):
    required = ["storeHash", "currency", "region", "eligibleStatusIds", "itemMap", "allowNewCustomers",
                "customerNameSource", "paidOrderTreatment", "purchaseOrders", "suppliers", "invoicePrefix"]
    if any(k not in c for k in required):
        return "incomplete_config"
    if (not isinstance(c["storeHash"], str) or not STORE.fullmatch(c["storeHash"])
        or c["currency"] not in CURRENCIES or c["region"] not in ["US", "non_US"]
        or c["customerNameSource"] not in NAME_SOURCES or c["paidOrderTreatment"] not in PAID_TREATMENTS
        or not isinstance(c["itemMap"], dict) or not isinstance(c["suppliers"], dict)
        or not isinstance(c["invoicePrefix"], str) or not PREFIX.fullmatch(c["invoicePrefix"])
        or not isinstance(c["eligibleStatusIds"], list) or not c["eligibleStatusIds"]
        or any(type(s) is not int or s in [0, 4, 5, 6, 14] for s in c["eligibleStatusIds"])
        or any(type(c[k]) is not bool for k in ["allowNewCustomers", "purchaseOrders"])):
        return "invalid_config"
    if c["region"] == "non_US" and not identity_text(c.get("zeroTaxCodeName")):
        return "zero_tax_code_required_for_non_us"
    return None


def catalog(data, field):
    """Returns (records|None, state). Absent means no QuickBooks connection."""
    if data.get(field) is None:
        return None, "none"
    records = data[field]
    if not isinstance(records, list) or len(records) > 100000 or any(not isinstance(r, dict) for r in records):
        return False, "invalid"
    for record in records:
        if "PrimaryEmailAddr" in record and not isinstance(record["PrimaryEmailAddr"], dict):
            return False, "invalid"
        if "CurrencyRef" in record and not isinstance(record["CurrencyRef"], dict):
            return False, "invalid"
    return records, "complete" if data.get(field + "Complete") is True else "incomplete"


def plan(data):
    if not isinstance(data, dict):
        return {"status": "invalid", "reason": "object_required", "orders": []}
    c = data.get("config")
    if not isinstance(c, dict):
        return {"status": "invalid", "reason": "config_required", "orders": []}
    problem = config_problem(c)
    if problem:
        return {"status": "invalid", "reason": problem, "orders": []}
    if not isinstance(data.get("orders"), list) or len(data["orders"]) > MAX_ORDERS:
        return {"status": "invalid", "reason": "bounded_orders_required", "orders": []}
    customers, customer_state = catalog(data, "customers")
    vendors, vendor_state = catalog(data, "vendors")
    if customer_state == "invalid" or vendor_state == "invalid":
        return {"status": "invalid", "reason": "invalid_catalog_record", "orders": []}
    # Vendor lookups are advisory; an incomplete list cannot prove absence.
    vendor_lookup = vendors if vendor_state == "complete" else None
    # A batch must never choose an accounting snapshot by input order.
    snapshots = {}
    for raw in data["orders"]:
        if isinstance(raw, dict):
            snapshots.setdefault(str(raw.get("id", "")), set()).add(digest(raw))
    conflicting_orders = {oid for oid, hashes in snapshots.items() if len(hashes) > 1}
    seen = set()
    results = []
    for raw in data["orders"]:
        oid = str(raw.get("id", "")) if isinstance(raw, dict) else ""
        if oid in conflicting_orders:
            results.append(review(oid, "conflicting_order_snapshots"))
            continue
        if oid in seen:
            results.append(review(oid, "duplicate_order_in_batch"))
            continue
        seen.add(oid)
        results.append(plan_order(raw, c, customers, customer_state, vendor_lookup))
    # Two different buyers must never be folded into one new QuickBooks name.
    # One buyer email must never become two new QuickBooks customers either.
    new_names = {}
    new_emails = {}
    for r in results:
        if r["status"] == "planned" and r["customer"]["match"] in ["new", "unverified"]:
            new_names.setdefault(r["customer"]["name"].casefold(), set()).add(r["customer"]["details"]["email"])
            new_emails.setdefault(r["customer"]["details"]["email"], set()).add(r["customer"]["name"].casefold())
    clashes = {name for name, emails in new_names.items() if len(emails) > 1}
    email_clashes = {email for email, names in new_emails.items() if len(names) > 1}

    def conflict(r):
        if r["status"] != "planned" or r["customer"]["match"] not in ["new", "unverified"]:
            return None
        if r["customer"]["name"].casefold() in clashes:
            return "new_customer_name_conflict"
        if r["customer"]["details"]["email"] in email_clashes:
            return "new_customer_email_conflict"
        return None
    results = [review(r["orderId"], conflict(r)) if conflict(r) else r for r in results]
    counts = {s: sum(r["status"] == s for r in results) for s in ["planned", "review"]}
    return {"version": VERSION, "status": "planned", "orders": results, "counts": counts,
            "customerLookup": customer_state, "vendorLookup": vendor_state,
            "summary": f"BigCommerce -> QuickBooks import plan: {counts['planned']} ready for import files, {counts['review']} need review. No QuickBooks or BigCommerce writes."}


if __name__ == "__main__":
    # JSON parsing is an input-boundary exception, never an accounting branch.
    try:
        incoming = json.load(sys.stdin)
    except (ValueError, UnicodeDecodeError, RecursionError):
        print(json.dumps({"status": "invalid", "reason": "invalid_json", "orders": []}))
        sys.exit(2)
    print(json.dumps(plan(incoming), ensure_ascii=True, allow_nan=False, separators=(",", ":")))
