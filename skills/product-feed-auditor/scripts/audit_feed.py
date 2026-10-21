#!/usr/bin/env python3
"""Audit a Google Merchant Center / Shopping product feed before submission.

Reads a local feed export (CSV or TSV) and reports, per rule, the exact item
ids that carry a disapproval or limited-visibility risk. Structural problems
with the file itself (missing required columns, empty feed) exit non-zero;
bad values *inside* the feed are the product of this tool and are reported as
findings, not crashes.

Stdlib only. No network access: image and landing-page URLs are checked by
structure and pattern, never fetched.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

# --- Merchant Center thresholds ------------------------------------------
# Sources: Merchant Center product data specification and editorial policy.
TITLE_HARD_MAX = 150  # spec limit; longer values are truncated or rejected
TITLE_DISPLAY_MAX = 70  # practical truncation point in Shopping surfaces
TITLE_MIN = 20  # below this a title carries almost no matchable keywords
DESCRIPTION_HARD_MAX = 5000  # spec limit
DESCRIPTION_THIN = 190  # below this the description adds little matching signal
CAPS_MIN_LETTERS = 12  # ignore short strings when judging capitalisation
CAPS_RATIO = 0.70  # share of letters uppercase that reads as shouting
TITLE_TOKEN_REPEAT = 3  # same meaningful token 3x in a title is stuffing
DESCRIPTION_TOKEN_REPEAT = 8
SALE_MIN_DISCOUNT = Decimal("0.05")  # below 5% Google shows no sale annotation
SALE_MAX_DISCOUNT = Decimal("0.90")  # above 90% triggers price-accuracy review

AVAILABILITY_VALUES = {"in_stock", "out_of_stock", "preorder", "backorder"}
CONDITION_VALUES = {"new", "refurbished", "used"}
IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
)
PLACEHOLDER_IMAGE = re.compile(
    r"(placeholder|no[-_]?image|noimage|coming[-_]?soon|default[-_]?product|dummy|sample[-_]?image)",
    re.IGNORECASE,
)
NON_PUBLIC_HOST = re.compile(
    r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|.*\.local|.*\.internal|staging\..*|dev\..*)$",
    re.IGNORECASE,
)
UNSAFE_URL_CHARS = re.compile(r"[\s<>\"{}|\\^`\[\]]")
PROMO_TITLE = re.compile(
    r"(free\s+shipping|best\s+price|lowest\s+price|buy\s+now|order\s+now|shop\s+now"
    r"|limited\s+time|act\s+now|on\s+sale|clearance|\bsale\b|\bdeal\b|\bcheap\b"
    r"|\d{1,3}\s*%\s*off|\bbogo\b|hot\s+deal|best\s+seller)",
    re.IGNORECASE,
)
GIMMICK_CHARS = re.compile(
    r"[★☆❤✔➜»►•]{1,}|[\U0001F000-\U0001FAFF]"
)
HTML_TAG = re.compile(r"<\s*/?\s*(p|br|div|span|ul|li|ol|strong|b|i|em|table|tr|td|h[1-6])\b[^>]*>", re.IGNORECASE)
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "your", "our", "you",
    "are", "was", "has", "have", "will", "can", "all", "any", "each", "into",
    "out", "per", "its", "not", "but", "one", "two", "size", "new", "more",
}

REQUIRED_COLUMNS = [
    "id",
    "title",
    "description",
    "link",
    "image_link",
    "availability",
    "price",
    "brand",
    "condition",
]
OPTIONAL_COLUMNS = [
    "sale_price",
    "sale_price_effective_date",
    "gtin",
    "mpn",
    "identifier_exists",
    "google_product_category",
    "product_type",
    "item_group_id",
    "additional_image_link",
]

SEVERITIES = ("critical", "warning", "info")
SEVERITY_RANK = {name: index for index, name in enumerate(SEVERITIES)}


@dataclass(frozen=True)
class Rule:
    code: str
    severity: str
    headline: str
    risk: str


RULES: "OrderedDict[str, Rule]" = OrderedDict()


def rule(code: str, severity: str, headline: str, risk: str) -> Rule:
    entry = Rule(code=code, severity=severity, headline=headline, risk=risk)
    RULES[code] = entry
    return entry


# Identity and structure
R_DUPLICATE_ID = rule(
    "ID-DUPLICATE", "critical", "Duplicate item id",
    "Merchant Center keeps only the last row for a duplicated id; the earlier variants silently disappear from Shopping.",
)
R_ID_CASE = rule(
    "ID-CASE-COLLISION", "warning", "Item ids differ only by letter case",
    "Ids are case sensitive in Merchant Center but not in most ERPs, so these pairs drift apart between systems.",
)
R_MISSING_REQUIRED = rule(
    "ATTR-MISSING", "critical", "Required attribute is empty",
    "Items missing a required attribute are disapproved on upload and never serve.",
)
R_MISSING_IDENTIFIER = rule(
    "ID-NO-UNIQUE-IDENTIFIER", "critical", "No GTIN or MPN and identifier_exists is not 'no'",
    "Branded products without a unique product identifier are disapproved for 'Missing identifier'.",
)
R_GTIN_INVALID = rule(
    "ID-GTIN-INVALID", "critical", "GTIN is malformed or fails its check digit",
    "An invalid GTIN is disapproved for 'Incorrect identifier' and blocks matching to the product catalogue.",
)
R_GTIN_DUPLICATE = rule(
    "ID-GTIN-REUSED", "warning", "Same GTIN on products that are not variants of each other",
    "Reused GTINs merge distinct products in the Shopping catalogue and are a common ERP copy-paste error.",
)
R_AVAILABILITY_VALUE = rule(
    "ATTR-AVAILABILITY-INVALID", "critical", "availability is not a supported value",
    "Unsupported enum values are rejected at upload; the item is disapproved.",
)
R_CONDITION_VALUE = rule(
    "ATTR-CONDITION-INVALID", "critical", "condition is not a supported value",
    "Unsupported enum values are rejected at upload; the item is disapproved.",
)

# Title and description quality
R_TITLE_TOO_LONG = rule(
    "TITLE-OVER-LIMIT", "critical", "Title exceeds the 150 character spec limit",
    "Titles over the spec limit are truncated or rejected, and the trailing attributes never match a query.",
)
R_TITLE_TRUNCATED = rule(
    "TITLE-DISPLAY-TRUNCATION", "info", "Title longer than 70 characters",
    "Everything past roughly 70 characters is cut in most Shopping placements; front-load brand, product, key attribute.",
)
R_TITLE_THIN = rule(
    "TITLE-THIN", "warning", "Title under 20 characters",
    "Too few keywords to match long-tail queries; impressions stay far below category norms.",
)
R_TITLE_CAPS = rule(
    "TITLE-EXCESSIVE-CAPS", "critical", "Title is mostly capital letters",
    "Excessive capitalisation is an editorial policy violation and is disapproved.",
)
R_TITLE_PROMO = rule(
    "TITLE-PROMOTIONAL-TEXT", "critical", "Title contains promotional text",
    "Promotional wording in the title is an editorial policy violation and is disapproved.",
)
R_TITLE_GIMMICK = rule(
    "TITLE-GIMMICK-CHARACTERS", "critical", "Title contains emoji or decorative symbols",
    "Gimmicky characters are an editorial policy violation and are disapproved.",
)
R_TITLE_STUFFED = rule(
    "TITLE-KEYWORD-STUFFING", "warning", "Title repeats the same keyword",
    "Repetition is treated as keyword stuffing; it wastes display width and can trigger editorial review.",
)
R_DESC_TOO_LONG = rule(
    "DESC-OVER-LIMIT", "critical", "Description exceeds the 5000 character spec limit",
    "Over-length descriptions are truncated or rejected at upload.",
)
R_DESC_THIN = rule(
    "DESC-THIN", "warning", "Description under 190 characters",
    "Thin descriptions reduce query matching; Google recommends a full paragraph of product detail.",
)
R_DESC_HTML = rule(
    "DESC-HTML-MARKUP", "critical", "Description contains HTML markup",
    "HTML is not allowed in the description attribute and is disapproved for invalid formatting.",
)
R_DESC_CAPS = rule(
    "DESC-EXCESSIVE-CAPS", "warning", "Description is mostly capital letters",
    "Excessive capitalisation can trip editorial review on the whole item.",
)
R_DESC_STUFFED = rule(
    "DESC-KEYWORD-STUFFING", "warning", "Description repeats the same keyword heavily",
    "Reads as keyword stuffing to editorial review and to shoppers.",
)

# URLs
R_IMAGE_NOT_ABSOLUTE = rule(
    "IMG-NOT-ABSOLUTE", "critical", "image_link is not an absolute http(s) URL",
    "Relative or malformed image URLs cannot be crawled; the item is disapproved for 'Image not found'.",
)
R_IMAGE_UNSAFE_CHARS = rule(
    "IMG-UNENCODED-CHARS", "critical", "image_link contains spaces or unencoded characters",
    "The crawler cannot resolve the URL; the item is disapproved for 'Invalid image'.",
)
R_IMAGE_INSECURE = rule(
    "IMG-INSECURE-SCHEME", "warning", "image_link uses http instead of https",
    "Mixed-content images are rejected by some surfaces and downgrade trust signals.",
)
R_IMAGE_EXTENSION = rule(
    "IMG-UNSUPPORTED-EXTENSION", "warning", "image_link has no supported image extension",
    "If the URL does not resolve to a supported format the crawl fails and the item is disapproved.",
)
R_IMAGE_PLACEHOLDER = rule(
    "IMG-PLACEHOLDER", "critical", "image_link looks like a placeholder asset",
    "Placeholder or 'no image' art is disapproved for 'Generic image'.",
)
R_IMAGE_NON_PUBLIC = rule(
    "IMG-NON-PUBLIC-HOST", "critical", "image_link points at a non-public host",
    "Staging, local, and internal hosts are unreachable to the crawler; the item is disapproved.",
)
R_LINK_INVALID = rule(
    "LINK-INVALID", "critical", "link is not an absolute https URL",
    "The landing page cannot be verified; the item is disapproved for an invalid destination.",
)
R_LINK_DOMAIN = rule(
    "LINK-DOMAIN-MISMATCH", "critical", "link host does not match the claimed store domain",
    "Landing pages outside the claimed and verified domain are disapproved.",
)

# Price coherence
R_PRICE_UNPARSEABLE = rule(
    "PRICE-UNPARSEABLE", "critical", "price is not a readable amount",
    "Unparseable prices are rejected at upload; the item never enters the catalogue.",
)
R_PRICE_NON_POSITIVE = rule(
    "PRICE-NON-POSITIVE", "critical", "price is zero or negative",
    "Zero-price items are disapproved and, when they do serve, they are an obvious revenue leak.",
)
R_PRICE_NO_CURRENCY = rule(
    "PRICE-NO-CURRENCY", "warning", "price has no ISO currency code",
    "Without an explicit currency the value is interpreted with the account default, which breaks multi-country feeds.",
)
R_SALE_NOT_LOWER = rule(
    "SALE-NOT-LOWER", "critical", "sale_price is not lower than price",
    "Merchant Center rejects a sale price at or above the base price for 'Invalid sale price'.",
)
R_SALE_CURRENCY = rule(
    "SALE-CURRENCY-MISMATCH", "critical", "sale_price currency differs from price currency",
    "Mixed currencies on one item are rejected at upload.",
)
R_SALE_TOO_DEEP = rule(
    "SALE-IMPLAUSIBLE-DEPTH", "warning", "Discount deeper than 90%",
    "Discounts this deep trigger price-accuracy review and are frequently a pricing-data bug.",
)
R_SALE_TOO_SHALLOW = rule(
    "SALE-BELOW-ANNOTATION-FLOOR", "warning", "Discount shallower than 5%",
    "Google shows no sale annotation below a 5% discount, so the markdown costs margin and buys no badge.",
)
R_SALE_WINDOW_PAST = rule(
    "SALE-WINDOW-EXPIRED", "warning", "sale_price_effective_date window ended before the as-of date",
    "The sale price is inert; shoppers see the base price while the feed still carries markdown data.",
)
R_SALE_WINDOW_INVALID = rule(
    "SALE-WINDOW-INVALID", "critical", "sale_price_effective_date is malformed",
    "A malformed window is rejected and the sale price is dropped.",
)


@dataclass
class Finding:
    code: str
    item_ids: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)


class FeedError(Exception):
    """Structural problem with the file itself."""


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def parse_as_of(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        fail(f"--as-of must be YYYY-MM-DD, got {value!r}")
        raise  # unreachable


def read_feed(path: str, delimiter: str) -> tuple[list[dict[str, str]], list[str]]:
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        fail(f"cannot open feed file {path}: {exc.strerror}")
        raise
    with handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            fail(f"{path} is empty: expected a header row with columns {', '.join(REQUIRED_COLUMNS)}")
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in REQUIRED_COLUMNS if name not in header]
        if missing:
            fail(
                f"{path} is missing required column(s): {', '.join(missing)}. "
                f"Required columns are: {', '.join(REQUIRED_COLUMNS)}"
            )
        rows: list[dict[str, str]] = []
        for raw in reader:
            row = {
                (key or "").strip(): (value or "").strip()
                for key, value in raw.items()
                if key is not None
            }
            rows.append(row)
    if not rows:
        fail(f"{path} has a header but no product rows")
    return rows, header


PRICE_RE = re.compile(r"^\s*(?P<amount>-?[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<currency>[A-Za-z]{3})?\s*$")
PRICE_RE_PREFIX = re.compile(r"^\s*(?P<currency>[A-Za-z]{3})\s*(?P<amount>-?[0-9][0-9,]*(?:\.[0-9]+)?)\s*$")


def parse_price(value: str) -> tuple[Decimal | None, str | None]:
    """Return (amount, currency). amount is None when the value is unreadable."""
    if not value:
        return None, None
    match = PRICE_RE.match(value) or PRICE_RE_PREFIX.match(value)
    if not match:
        return None, None
    try:
        amount = Decimal(match.group("amount").replace(",", ""))
    except InvalidOperation:
        return None, None
    currency = match.group("currency")
    return amount, currency.upper() if currency else None


def gtin_is_valid(value: str) -> bool:
    digits = value.strip()
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    body = [int(char) for char in digits[:-1]]
    check = int(digits[-1])
    # Weights alternate 3/1 from the rightmost body digit.
    total = 0
    for index, digit in enumerate(reversed(body)):
        total += digit * (3 if index % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


def caps_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if len(letters) < CAPS_MIN_LETTERS:
        return 0.0
    upper = sum(1 for char in letters if char.isupper())
    return upper / len(letters)


def repeated_token(text: str, threshold: int) -> tuple[str, int] | None:
    tokens = [
        token
        for token in WORD.findall(text.lower())
        if len(token) >= 4 and token not in STOPWORDS and not token.isdigit()
    ]
    if not tokens:
        return None
    counts = Counter(tokens)
    # Deterministic: highest count, then alphabetical.
    best = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0]
    return best if best[1] >= threshold else None


def split_url(url: str) -> tuple[str, str, str]:
    """Return (scheme, host, path) without importing a URL parser's networking side."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return "", "", url
    host, slash, path = rest.partition("/")
    host = host.split("@")[-1].split(":")[0]
    return scheme.lower(), host.lower(), (slash + path) if slash else ""


class Auditor:
    def __init__(self, as_of: date, domain: str | None, max_ids: int) -> None:
        self.as_of = as_of
        self.domain = domain.lower().lstrip(".") if domain else None
        self.max_ids = max_ids
        self.findings: "OrderedDict[str, Finding]" = OrderedDict()
        self.items_with_critical: set[str] = set()
        self.items_with_warning: set[str] = set()

    def add(self, code: str, item_id: str, detail: str = "", *, also: list[str] | None = None) -> None:
        """Record one finding. `also` names further ids involved in the same
        finding (the other side of a duplicate pair); each is credited in the
        item list and the summary, while the detail line is recorded once."""
        finding = self.findings.get(code)
        if finding is None:
            finding = Finding(code=code)
            self.findings[code] = finding
        involved = [item_id, *(also or [])]
        finding.item_ids.extend(involved)
        if detail:
            finding.details.append(detail)
        severity = RULES[code].severity
        if severity == "critical":
            self.items_with_critical.update(involved)
        elif severity == "warning":
            self.items_with_warning.update(involved)

    # -- per-row checks ---------------------------------------------------
    def check_row(self, row: dict[str, str], row_number: int) -> None:
        item_id = row.get("id") or f"<row {row_number}>"

        for column in REQUIRED_COLUMNS:
            if column == "id":
                continue
            if not row.get(column):
                self.add(R_MISSING_REQUIRED.code, item_id, f"{item_id}: {column}")

        self.check_identifiers(row, item_id)
        self.check_enums(row, item_id)
        self.check_title(row, item_id)
        self.check_description(row, item_id)
        self.check_image(row, item_id)
        self.check_link(row, item_id)
        self.check_price(row, item_id)

    def check_identifiers(self, row: dict[str, str], item_id: str) -> None:
        gtin = row.get("gtin", "")
        mpn = row.get("mpn", "")
        identifier_exists = row.get("identifier_exists", "").lower()
        if gtin and not gtin_is_valid(gtin):
            self.add(R_GTIN_INVALID.code, item_id, f"{item_id}: gtin={gtin}")
        if not gtin and not mpn and identifier_exists != "no":
            self.add(R_MISSING_IDENTIFIER.code, item_id, f"{item_id}: no gtin, no mpn")

    def check_enums(self, row: dict[str, str], item_id: str) -> None:
        availability = row.get("availability", "").lower()
        if availability and availability not in AVAILABILITY_VALUES:
            self.add(R_AVAILABILITY_VALUE.code, item_id, f"{item_id}: availability={row['availability']}")
        condition = row.get("condition", "").lower()
        if condition and condition not in CONDITION_VALUES:
            self.add(R_CONDITION_VALUE.code, item_id, f"{item_id}: condition={row['condition']}")

    def check_title(self, row: dict[str, str], item_id: str) -> None:
        title = row.get("title", "")
        if not title:
            return
        length = len(title)
        if length > TITLE_HARD_MAX:
            self.add(R_TITLE_TOO_LONG.code, item_id, f"{item_id}: {length} chars")
        elif length > TITLE_DISPLAY_MAX:
            self.add(R_TITLE_TRUNCATED.code, item_id, f"{item_id}: {length} chars")
        if length < TITLE_MIN:
            self.add(R_TITLE_THIN.code, item_id, f"{item_id}: {length} chars")
        if caps_ratio(title) > CAPS_RATIO:
            self.add(R_TITLE_CAPS.code, item_id, f"{item_id}: {title[:48]}")
        promo = PROMO_TITLE.search(title)
        if promo:
            self.add(R_TITLE_PROMO.code, item_id, f"{item_id}: '{promo.group(0).strip()}'")
        if GIMMICK_CHARS.search(title):
            self.add(R_TITLE_GIMMICK.code, item_id, f"{item_id}: {title[:48]}")
        repeat = repeated_token(title, TITLE_TOKEN_REPEAT)
        if repeat:
            self.add(R_TITLE_STUFFED.code, item_id, f"{item_id}: '{repeat[0]}' x{repeat[1]}")

    def check_description(self, row: dict[str, str], item_id: str) -> None:
        description = row.get("description", "")
        if not description:
            return
        length = len(description)
        if length > DESCRIPTION_HARD_MAX:
            self.add(R_DESC_TOO_LONG.code, item_id, f"{item_id}: {length} chars")
        if length < DESCRIPTION_THIN:
            self.add(R_DESC_THIN.code, item_id, f"{item_id}: {length} chars")
        if HTML_TAG.search(description):
            self.add(R_DESC_HTML.code, item_id, f"{item_id}: {HTML_TAG.search(description).group(0)}")
        if caps_ratio(description) > CAPS_RATIO:
            self.add(R_DESC_CAPS.code, item_id, f"{item_id}: {description[:48]}")
        repeat = repeated_token(description, DESCRIPTION_TOKEN_REPEAT)
        if repeat:
            self.add(R_DESC_STUFFED.code, item_id, f"{item_id}: '{repeat[0]}' x{repeat[1]}")

    def check_image(self, row: dict[str, str], item_id: str) -> None:
        url = row.get("image_link", "")
        if not url:
            return
        if UNSAFE_URL_CHARS.search(url):
            self.add(R_IMAGE_UNSAFE_CHARS.code, item_id, f"{item_id}: {url[:60]}")
            return
        scheme, host, path = split_url(url)
        if scheme not in ("http", "https") or not host:
            self.add(R_IMAGE_NOT_ABSOLUTE.code, item_id, f"{item_id}: {url[:60]}")
            return
        if NON_PUBLIC_HOST.match(host):
            self.add(R_IMAGE_NON_PUBLIC.code, item_id, f"{item_id}: host {host}")
        if scheme == "http":
            self.add(R_IMAGE_INSECURE.code, item_id, f"{item_id}: {url[:60]}")
        if PLACEHOLDER_IMAGE.search(url):
            self.add(R_IMAGE_PLACEHOLDER.code, item_id, f"{item_id}: {url[:60]}")
        bare_path = path.split("?")[0].split("#")[0].lower()
        if not bare_path.endswith(IMAGE_EXTENSIONS):
            self.add(R_IMAGE_EXTENSION.code, item_id, f"{item_id}: {url[:60]}")

    def check_link(self, row: dict[str, str], item_id: str) -> None:
        url = row.get("link", "")
        if not url:
            return
        if UNSAFE_URL_CHARS.search(url):
            self.add(R_LINK_INVALID.code, item_id, f"{item_id}: {url[:60]}")
            return
        scheme, host, _path = split_url(url)
        if scheme != "https" or not host:
            self.add(R_LINK_INVALID.code, item_id, f"{item_id}: {url[:60]}")
            return
        if self.domain and not (host == self.domain or host.endswith("." + self.domain)):
            self.add(R_LINK_DOMAIN.code, item_id, f"{item_id}: host {host}")

    def check_price(self, row: dict[str, str], item_id: str) -> None:
        raw_price = row.get("price", "")
        price, currency = parse_price(raw_price)
        if raw_price and price is None:
            self.add(R_PRICE_UNPARSEABLE.code, item_id, f"{item_id}: price={raw_price}")
        if price is not None:
            if price <= 0:
                self.add(R_PRICE_NON_POSITIVE.code, item_id, f"{item_id}: price={raw_price}")
            if currency is None:
                self.add(R_PRICE_NO_CURRENCY.code, item_id, f"{item_id}: price={raw_price}")

        raw_sale = row.get("sale_price", "")
        if not raw_sale:
            self.check_sale_window(row, item_id, has_sale_price=False)
            return
        sale, sale_currency = parse_price(raw_sale)
        if sale is None:
            self.add(R_PRICE_UNPARSEABLE.code, item_id, f"{item_id}: sale_price={raw_sale}")
            return
        if currency and sale_currency and currency != sale_currency:
            self.add(R_SALE_CURRENCY.code, item_id, f"{item_id}: {currency} vs {sale_currency}")
        if price is not None and price > 0:
            if sale >= price:
                self.add(R_SALE_NOT_LOWER.code, item_id, f"{item_id}: {raw_sale} >= {raw_price}")
            else:
                depth = (price - sale) / price
                if depth > SALE_MAX_DISCOUNT:
                    self.add(R_SALE_TOO_DEEP.code, item_id, f"{item_id}: {depth * 100:.1f}% off")
                elif depth < SALE_MIN_DISCOUNT:
                    self.add(R_SALE_TOO_SHALLOW.code, item_id, f"{item_id}: {depth * 100:.1f}% off")
        self.check_sale_window(row, item_id, has_sale_price=True)

    def check_sale_window(self, row: dict[str, str], item_id: str, has_sale_price: bool) -> None:
        window = row.get("sale_price_effective_date", "")
        if not window:
            return
        parts = [part.strip() for part in window.split("/")]
        if len(parts) != 2:
            self.add(R_SALE_WINDOW_INVALID.code, item_id, f"{item_id}: {window}")
            return
        try:
            start = date.fromisoformat(parts[0][:10])
            end = date.fromisoformat(parts[1][:10])
        except ValueError:
            self.add(R_SALE_WINDOW_INVALID.code, item_id, f"{item_id}: {window}")
            return
        if end < start:
            self.add(R_SALE_WINDOW_INVALID.code, item_id, f"{item_id}: end before start")
            return
        if has_sale_price and end < self.as_of:
            self.add(R_SALE_WINDOW_PAST.code, item_id, f"{item_id}: ended {end.isoformat()}")

    def check_duplicates(self, rows: list[dict[str, str]]) -> None:
        seen: Counter[str] = Counter()
        order: list[str] = []
        for row in rows:
            item_id = row.get("id", "")
            if not item_id:
                continue
            if item_id not in seen:
                order.append(item_id)
            seen[item_id] += 1
        for item_id in order:
            if seen[item_id] > 1:
                self.add(R_DUPLICATE_ID.code, item_id, f"{item_id}: {seen[item_id]} rows")
        lowered: "OrderedDict[str, list[str]]" = OrderedDict()
        for item_id in order:
            lowered.setdefault(item_id.lower(), []).append(item_id)
        for variants in lowered.values():
            if len(variants) > 1:
                self.add(R_ID_CASE.code, variants[0], f"{' / '.join(variants)}", also=variants[1:])

        # A GTIN identifies one sellable product. Two different item ids sharing
        # one GTIN is only legitimate when they are the same product in the same
        # variant group, which the feed states through item_group_id.
        by_gtin: "OrderedDict[str, list[tuple[str, str]]]" = OrderedDict()
        for row in rows:
            gtin = row.get("gtin", "")
            item_id = row.get("id", "")
            if not gtin or not item_id:
                continue
            by_gtin.setdefault(gtin, []).append((item_id, row.get("item_group_id", "")))
        for gtin, entries in by_gtin.items():
            distinct_ids = sorted({item_id for item_id, _group in entries})
            if len(distinct_ids) < 2:
                continue
            groups = {group for _item_id, group in entries}
            if len(groups) == 1 and "" not in groups:
                continue  # same variant group, stated deliberately
            self.add(
                R_GTIN_DUPLICATE.code,
                distinct_ids[0],
                f"gtin {gtin} on {', '.join(distinct_ids)}",
                also=distinct_ids[1:],
            )


def unique_item_ids(rows: list[dict[str, str]]) -> set[str]:
    """Distinct item ids, the unit every summary count and share is based on.

    A duplicated id is one item however many rows carry it; a row with a blank
    id is its own item, keyed the same way check_row keys it."""
    return {row.get("id") or f"<row {row_number}>" for row_number, row in enumerate(rows, start=2)}


def coverage_table(rows: list[dict[str, str]], header: list[str]) -> list[tuple[str, int, int]]:
    tracked = REQUIRED_COLUMNS + [name for name in OPTIONAL_COLUMNS if name in header]
    table: list[tuple[str, int, int]] = []
    for column in tracked:
        present = sum(1 for row in rows if row.get(column))
        table.append((column, present, len(rows) - present))
    return table


def format_ids(ids: list[str], max_ids: int) -> str:
    unique: list[str] = []
    seen: set[str] = set()
    for item_id in ids:
        if item_id not in seen:
            seen.add(item_id)
            unique.append(item_id)
    if len(unique) <= max_ids:
        return ", ".join(unique)
    shown = ", ".join(unique[:max_ids])
    return f"{shown}, +{len(unique) - max_ids} more"


def render_text(
    feed_path: str,
    as_of: date,
    rows: list[dict[str, str]],
    header: list[str],
    auditor: Auditor,
) -> str:
    lines: list[str] = []
    total = len(rows)
    items = len(unique_item_ids(rows))
    lines.append("PRODUCT FEED AUDIT")
    lines.append(f"feed: {feed_path}   rows: {total}   unique item ids: {items}   as of: {as_of.isoformat()}")
    lines.append("=" * 72)
    lines.append("")

    lines.append("ATTRIBUTE COVERAGE (per row)")
    lines.append(f"  {'attribute':<28}{'present':>8}{'missing':>9}{'coverage':>10}")
    for column, present, missing in coverage_table(rows, header):
        pct = (present / total) * 100 if total else 0.0
        lines.append(f"  {column:<28}{present:>8}{missing:>9}{pct:>9.1f}%")
    lines.append("")

    counts = {severity: 0 for severity in SEVERITIES}
    for code, finding in auditor.findings.items():
        counts[RULES[code].severity] += len(set(finding.item_ids))

    lines.append("FINDINGS BY SEVERITY")
    ordered = sorted(
        auditor.findings.items(),
        key=lambda pair: (SEVERITY_RANK[RULES[pair[0]].severity], pair[0]),
    )
    if not ordered:
        lines.append("  No findings. Feed passes every configured check.")
    current_severity = None
    for code, finding in ordered:
        spec = RULES[code]
        if spec.severity != current_severity:
            current_severity = spec.severity
            lines.append("")
            lines.append(f"-- {spec.severity.upper()} " + "-" * (68 - len(spec.severity)))
        affected = len(set(finding.item_ids))
        lines.append("")
        lines.append(f"  [{code}] {spec.headline}  ({affected} item(s))")
        lines.append(f"    risk: {spec.risk}")
        lines.append(f"    items: {format_ids(finding.item_ids, auditor.max_ids)}")
        for detail in finding.details[: auditor.max_ids]:
            lines.append(f"      - {detail}")
        if len(finding.details) > auditor.max_ids:
            lines.append(f"      - +{len(finding.details) - auditor.max_ids} more")
    lines.append("")
    lines.append("=" * 72)

    at_risk = len(auditor.items_with_critical)
    warned = len(auditor.items_with_warning - auditor.items_with_critical)
    clean = items - len(auditor.items_with_critical | auditor.items_with_warning)
    risk_pct = (at_risk / items) * 100 if items else 0.0
    lines.append(f"SUMMARY (by unique item id; {items} items)")
    lines.append(f"  items at disapproval risk (>=1 critical): {at_risk} ({risk_pct:.1f}% of items)")
    lines.append(f"  items with warnings only:                 {warned}")
    lines.append(f"  items clean:                              {clean}")
    lines.append(f"  critical findings: {counts['critical']}   warnings: {counts['warning']}   info: {counts['info']}")
    lines.append("")
    lines.append("Fix critical findings before the next feed fetch; they cost impressions immediately.")
    lines.append("Image and landing-page URLs were checked by structure only, never fetched.")
    return "\n".join(lines)


def render_json(
    feed_path: str,
    as_of: date,
    rows: list[dict[str, str]],
    header: list[str],
    auditor: Auditor,
) -> str:
    total = len(rows)
    items = len(unique_item_ids(rows))
    payload = {
        "feed": feed_path,
        "asOf": as_of.isoformat(),
        "rowCount": total,
        "itemCount": items,
        "coverage": [
            {
                "attribute": column,
                "present": present,
                "missing": missing,
                "coveragePct": round((present / total) * 100, 1) if total else 0.0,
            }
            for column, present, missing in coverage_table(rows, header)
        ],
        "findings": [
            {
                "code": code,
                "severity": RULES[code].severity,
                "headline": RULES[code].headline,
                "risk": RULES[code].risk,
                "itemCount": len(set(auditor.findings[code].item_ids)),
                "itemIds": sorted(set(auditor.findings[code].item_ids)),
                "details": auditor.findings[code].details,
            }
            for code in sorted(
                auditor.findings,
                key=lambda code: (SEVERITY_RANK[RULES[code].severity], code),
            )
        ],
        "summary": {
            "itemsAtDisapprovalRisk": len(auditor.items_with_critical),
            "itemsWithWarningsOnly": len(auditor.items_with_warning - auditor.items_with_critical),
            "itemsClean": items - len(auditor.items_with_critical | auditor.items_with_warning),
        },
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a Merchant Center product feed offline.")
    parser.add_argument("--feed", required=True, help="path to the feed export (CSV or TSV)")
    parser.add_argument("--as-of", required=True, help="reference date, YYYY-MM-DD (used for sale windows)")
    parser.add_argument("--domain", help="claimed store domain, e.g. shop.example.com")
    parser.add_argument("--delimiter", default=",", help="field delimiter; use $'\\t' for TSV")
    parser.add_argument("--max-ids", type=int, default=8, help="item ids to print per finding")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--fail-on",
        choices=["none", "critical", "warning"],
        default="none",
        help="exit 1 when findings at or above this severity exist",
    )
    args = parser.parse_args(argv)

    if args.max_ids < 1:
        fail("--max-ids must be at least 1")
    delimiter = args.delimiter
    if delimiter == "\\t":
        delimiter = "\t"
    if len(delimiter) != 1:
        fail(f"--delimiter must be a single character, got {args.delimiter!r}")

    as_of = parse_as_of(args.as_of)
    rows, header = read_feed(args.feed, delimiter)

    auditor = Auditor(as_of=as_of, domain=args.domain, max_ids=args.max_ids)
    auditor.check_duplicates(rows)
    for index, row in enumerate(rows, start=2):  # row 1 is the header
        auditor.check_row(row, index)

    if args.json:
        print(render_json(args.feed, as_of, rows, header, auditor))
    else:
        print(render_text(args.feed, as_of, rows, header, auditor))

    if args.fail_on == "critical" and auditor.items_with_critical:
        return 1
    if args.fail_on == "warning" and (auditor.items_with_critical or auditor.items_with_warning):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
