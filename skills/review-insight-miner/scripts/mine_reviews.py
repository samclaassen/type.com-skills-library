#!/usr/bin/env python3
"""Cluster product reviews and support tickets into recurring themes by keyword co-occurrence.

Themes are discovered from the text itself: terms that keep appearing together
are merged into clusters by pointwise mutual information, every document is
assigned to its best-matching cluster, and each cluster is then classified as a
product defect, an expectation mismatch, a logistics problem, a service
problem, or a value objection. Themes are ranked by volume and by the star
rating they drag down, and each one carries a concrete listing-copy or product
fix.

Stdlib only, offline, deterministic. No model calls, no network, no clock read.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

ZERO = Decimal("0")
REQUIRED_COLUMNS = ["id", "source", "text"]
WORD_RE = re.compile(r"[a-z][a-z0-9'\-]*")

STOPWORDS = {
    "a", "about", "after", "again", "all", "also", "am", "an", "and", "any", "are", "around", "as",
    "at", "back", "be", "because", "been", "before", "being", "bit", "both", "but", "by", "can",
    "could", "day", "days", "did", "do", "does", "doing", "done", "dont", "down", "each", "even",
    "ever", "every", "far", "few", "first", "for", "from", "get", "getting", "give", "go", "going",
    "got", "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how",
    "however", "i", "if", "im", "in", "into", "is", "it", "its", "ive", "just", "keep", "know",
    "last", "least", "less", "let", "like", "little", "lot", "made", "make", "many", "may", "me",
    "might", "mine", "month", "months", "more", "most", "much", "my", "need", "never", "new",
    "no", "not", "now", "of", "off", "on", "once", "one", "only", "or", "other", "our", "out",
    "over", "own", "put", "quite", "rather", "really", "right", "said", "same", "say", "see",
    "seen", "she", "should", "since", "so", "some", "something", "still", "such", "sure", "take",
    "than", "that", "the", "their", "them", "then", "there", "these", "they", "thing", "things",
    "think", "this", "those", "though", "three", "through", "time", "times", "to", "too", "try",
    "tried", "two", "under", "up", "us", "use", "used", "using", "very", "want", "wanted", "was",
    "way", "we", "week", "weeks", "well", "went", "were", "what", "when", "where", "which",
    "while", "who", "why", "will", "with", "without", "would", "year", "years", "yet", "you",
    "your", "yours",
    # Artefacts of conservative stemming, kept out of the vocabulary.
    "doe", "goe", "thi", "wa",
    # Support-export boilerplate: present in every ticket, informative in none.
    "customer", "report", "reported", "request", "requested", "requesting", "asking", "asks",
    "says", "regarding", "reference", "please", "hi", "hello", "thanks", "thank",
}

# Signal lexicons used only to LABEL a discovered cluster, never to create one.
SIGNALS: "OrderedDict[str, set[str]]" = OrderedDict(
    (
        (
            "PRODUCT DEFECT",
            {
                "break", "broke", "broken", "crack", "leak", "chip", "peel", "rip", "tear", "fray",
                "defect", "defective", "faulty", "fault", "malfunction", "dead", "rust", "mold",
                "mould", "jam", "stuck", "warp", "bend", "snap", "loose", "melt", "scratch", "dent",
                "seal", "seam", "stitch", "wobble", "rattle", "fail", "failed", "detach", "split",
                "bubble", "flake", "stop", "stopped", "quality", "fell",
            },
        ),
        (
            "EXPECTATION MISMATCH",
            {
                "expect", "expected", "photo", "picture", "pic", "image", "look", "looked", "color",
                "colour", "describe", "described", "description", "listing", "advertise",
                "advertised", "thought", "misleading", "smaller", "larger", "bigger", "tiny",
                "huge", "size", "sized", "fit", "fits", "dimension", "actual", "actually",
                "different", "differ", "match", "shade", "assume", "assumed", "narrow", "wide",
            },
        ),
        (
            "LOGISTICS",
            {
                "ship", "shipped", "shipping", "deliver", "delivered", "delivery", "arrive",
                "arrived", "late", "delay", "delayed", "package", "packaging", "box", "carton",
                "missing", "wrong", "track", "tracking", "carrier", "lost", "courier", "damaged",
                "crushed", "dented", "transit", "order", "ordered",
            },
        ),
        (
            "SERVICE",
            {
                "support", "service", "email", "emailed", "respond", "response", "reply", "replied",
                "refund", "refunded", "return", "returned", "replace", "replacement", "warranty",
                "contact", "contacted", "chat", "ticket", "agent", "rep", "resolve", "resolved",
                "helpful", "unhelpful", "ignored", "escalate",
            },
        ),
        (
            "USABILITY",
            {
                "clean", "cleaning", "wash", "washing", "dishwasher", "scrub", "brush", "rinse",
                "residue", "smell", "smells", "odor", "odour", "taste", "assemble", "assembly",
                "disassemble", "instruction", "instructions", "manual", "setup", "install",
                "confusing", "complicated", "fiddly", "awkward", "hassle", "chore", "maintain",
            },
        ),
        (
            "VALUE",
            {
                "price", "priced", "pricey", "expensive", "cheap", "worth", "value", "cost",
                "costly", "overpriced", "money", "premium", "afford", "budget",
            },
        ),
    )
)

ACTIONS: dict[str, tuple[str, str]] = {
    "PRODUCT DEFECT": (
        "Product and QA",
        "Pull a sample against the supplier spec for the failing part, quantify the failure rate by "
        "production lot, and decide between a design change and a supplier change. Until it ships, "
        "arm support with a no-questions replacement macro so this theme stops generating tickets.",
    ),
    "EXPECTATION MISMATCH": (
        "Listing copy and imagery",
        "The product is doing what it does; the page is promising something else. Put the disputed "
        "attribute in the spec table in explicit units, name it in the first three bullets, and add "
        "one photo or a short clip that shows it in context at real scale.",
    ),
    "LOGISTICS": (
        "Fulfilment and carrier",
        "Trace these orders back to the warehouse and carrier service level, check pack-out and "
        "carton spec for the damaged cases, and set delivery expectations on the product page and "
        "in the shipping confirmation rather than in the support queue.",
    ),
    "SERVICE": (
        "Support operations",
        "Measure first-response and resolution time for this theme specifically, then fix the top "
        "driver with a macro, a self-serve flow, or a policy change. Volume here is usually a "
        "symptom of one of the other themes going unaddressed.",
    ),
    "USABILITY": (
        "Product design and care content",
        "Customers can use the product but not live with it. Fix the part that makes daily care hard, "
        "and in the meantime put care and cleaning instructions on the product page, in the packaging "
        "insert, and in the post-purchase email rather than leaving them for the support queue.",
    ),
    "VALUE": (
        "Pricing and positioning",
        "Price objections cluster where the page has not justified the price. Lead with the durable "
        "differences, materials, warranty, cost per use, and reconsider the discount cadence that "
        "taught shoppers to wait.",
    ),
    "UNCLASSIFIED": (
        "Manual review",
        "This cluster's vocabulary does not match any signal lexicon. Read the verbatims before "
        "acting on it.",
    ),
}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def normalize(token: str) -> str:
    """Conservative, dependency-free stemming: plurals and common verb endings."""
    word = token.strip("'-")
    if word.endswith("'s"):
        word = word[:-2]
    if len(word) > 5 and word.endswith("ing"):
        stem = word[:-3]
        if len(stem) > 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            stem = stem[:-1]
        word = stem
    elif len(word) > 4 and word.endswith("ed"):
        stem = word[:-2]
        if len(stem) > 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            stem = stem[:-1]
        word = stem
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 4 and word.endswith(("ses", "shes", "ches", "xes")):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    return [normalize(match) for match in WORD_RE.findall(text.lower())]


# Stem -> how often each original spelling produced it, so reports can show the
# word a human wrote rather than the stem the clustering used.
SURFACE_FORMS: dict[str, dict[str, int]] = {}


def record_surfaces(text: str) -> None:
    for match in WORD_RE.findall(text.lower()):
        stem = normalize(match)
        counts = SURFACE_FORMS.setdefault(stem, {})
        counts[match] = counts.get(match, 0) + 1


def display_term(stem: str) -> str:
    counts = SURFACE_FORMS.get(stem)
    if not counts:
        return stem
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]


@dataclass
class Document:
    doc_id: str
    source: str
    text: str
    rating: int | None
    sku: str
    tokens: list[str] = field(default_factory=list)
    types: set[str] = field(default_factory=set)
    theme: int | None = None


@dataclass
class Theme:
    index: int
    terms: list[str]
    docs: list[Document] = field(default_factory=list)
    category: str = "UNCLASSIFIED"
    signal_counts: "OrderedDict[str, int]" = field(default_factory=OrderedDict)

    @property
    def label(self) -> str:
        return " / ".join(display_term(term) for term in self.terms[:3])

    @property
    def display_terms(self) -> list[str]:
        return [display_term(term) for term in self.terms]


def read_feedback(path: str) -> list[Document]:
    try:
        handle = open(path, "r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        fail(f"cannot open feedback file {path}: {exc.strerror}")
        raise
    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            fail(f"{path} is empty: expected a header row with {', '.join(REQUIRED_COLUMNS)}")
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in REQUIRED_COLUMNS if name not in header]
        if missing:
            fail(
                f"{path} is missing required column(s): {', '.join(missing)}. "
                f"Required columns are: {', '.join(REQUIRED_COLUMNS)}"
            )
        documents: list[Document] = []
        seen: set[str] = set()
        for row_number, raw in enumerate(reader, start=2):
            row = {(key or "").strip(): (value or "").strip() for key, value in raw.items() if key}
            where = f"{path} row {row_number}"
            doc_id = row.get("id", "")
            if not doc_id:
                fail(f"{where}: id is empty")
            if doc_id in seen:
                fail(f"{where}: id {doc_id!r} appears more than once")
            seen.add(doc_id)
            source = row.get("source", "").lower()
            if source not in ("review", "ticket"):
                fail(f"{where} column source: {row.get('source')!r} must be 'review' or 'ticket'")
            text = row.get("text", "")
            if not text:
                fail(f"{where}: text is empty for id {doc_id}")
            rating_raw = row.get("rating", "")
            rating: int | None = None
            if rating_raw:
                try:
                    rating = int(Decimal(rating_raw))
                except InvalidOperation:
                    fail(f"{where} column rating: {rating_raw!r} is not a number")
                if rating < 1 or rating > 5:
                    fail(f"{where} column rating: {rating} is outside the 1 to 5 scale")
            documents.append(
                Document(
                    doc_id=doc_id,
                    source=source,
                    text=" ".join(text.split()),
                    rating=rating,
                    sku=row.get("sku", ""),
                )
            )
    if not documents:
        fail(f"{path} has a header but no feedback rows")
    return documents


def build_vocabulary(documents: list[Document], min_docs: int, max_df_pct: Decimal) -> "OrderedDict[str, int]":
    df: dict[str, int] = {}
    for document in documents:
        record_surfaces(document.text)
        document.tokens = tokenize(document.text)
        document.types = {token for token in document.tokens if len(token) > 2 and token not in STOPWORDS}
        for term in document.types:
            df[term] = df.get(term, 0) + 1
    ceiling = (Decimal(len(documents)) * max_df_pct / Decimal(100))
    vocabulary = OrderedDict(
        sorted(
            (
                (term, count)
                for term, count in df.items()
                if count >= min_docs and Decimal(count) <= ceiling
            ),
            key=lambda pair: (-pair[1], pair[0]),
        )
    )
    return vocabulary


def co_occurrence(documents: list[Document], vocabulary: "OrderedDict[str, int]") -> dict[tuple[str, str], int]:
    pairs: dict[tuple[str, str], int] = {}
    for document in documents:
        present = sorted(term for term in document.types if term in vocabulary)
        for i, left in enumerate(present):
            for right in present[i + 1 :]:
                key = (left, right)
                pairs[key] = pairs.get(key, 0) + 1
    return pairs


def pmi_table(
    pairs: dict[tuple[str, str], int],
    vocabulary: "OrderedDict[str, int]",
    total_docs: int,
    min_pair_docs: int,
) -> dict[tuple[str, str], float]:
    table: dict[tuple[str, str], float] = {}
    for (left, right), count in pairs.items():
        if count < min_pair_docs:
            continue
        expected = (vocabulary[left] * vocabulary[right]) / total_docs
        if expected <= 0:
            continue
        # Rounded so that comparisons never depend on a last-bit libm difference.
        table[(left, right)] = round(math.log2(count / expected), 4)
    return table


def cluster_terms(
    vocabulary: "OrderedDict[str, int]",
    table: dict[tuple[str, str], float],
    min_pmi: float,
) -> list[list[str]]:
    """Greedy average-linkage agglomeration over the PMI table.

    Deterministic: candidate merges are compared on (score, sorted member
    names), never on dictionary order.
    """
    clusters: list[list[str]] = [[term] for term in vocabulary]

    def linkage(left: list[str], right: list[str]) -> float:
        total = 0.0
        for a in left:
            for b in right:
                key = (a, b) if a < b else (b, a)
                total += table.get(key, 0.0)
        return round(total / (len(left) * len(right)), 4)

    while len(clusters) > 1:
        best_score = min_pmi
        best_pair: tuple[int, int] | None = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                score = linkage(clusters[i], clusters[j])
                if score < best_score:
                    continue
                if best_pair is None or score > best_score or (
                    score == best_score
                    and (sorted(clusters[i] + clusters[j]) < sorted(clusters[best_pair[0]] + clusters[best_pair[1]]))
                ):
                    best_score = score
                    best_pair = (i, j)
        if best_pair is None:
            break
        i, j = best_pair
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]
    return clusters


def assign_documents(
    documents: list[Document],
    clusters: list[list[str]],
    vocabulary: "OrderedDict[str, int]",
    total_docs: int,
) -> None:
    idf = {
        term: round(math.log2(total_docs / count), 4) for term, count in vocabulary.items()
    }
    for document in documents:
        best_index: int | None = None
        best_score = 0.0
        for index, terms in enumerate(clusters):
            score = round(sum(idf[term] for term in terms if term in document.types), 4)
            if score > best_score:
                best_score = score
                best_index = index
        document.theme = best_index


def classify(theme: Theme) -> None:
    counts: "OrderedDict[str, int]" = OrderedDict((name, 0) for name in SIGNALS)
    for document in theme.docs:
        for token in document.tokens:
            for name, lexicon in SIGNALS.items():
                if token in lexicon:
                    counts[name] += 1
    theme.signal_counts = counts
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    theme.category = ranked[0][0] if ranked[0][1] > 0 else "UNCLASSIFIED"


def mean_rating(documents: list[Document]) -> Decimal | None:
    rated = [document.rating for document in documents if document.rating is not None]
    if not rated:
        return None
    return Decimal(sum(rated)) / Decimal(len(rated))


def quant(value: Decimal | None, places: str = "0.01") -> str:
    if value is None:
        return "n/a"
    return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def representative(theme: Theme, limit: int) -> list[Document]:
    scored = sorted(
        theme.docs,
        key=lambda document: (
            -len(document.types & set(theme.terms)),
            len(document.text),
            document.doc_id,
        ),
    )
    return scored[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cluster reviews and tickets into recurring, actionable themes.")
    parser.add_argument("--feedback", required=True, help="CSV of reviews and support tickets")
    parser.add_argument("--min-term-docs", type=int, default=3, help="documents a term must appear in to enter the vocabulary (default 3)")
    parser.add_argument("--max-term-df-pct", type=Decimal, default=Decimal("35"), help="drop terms appearing in more than this percent of documents (default 35)")
    parser.add_argument("--min-pair-docs", type=int, default=2, help="documents a term pair must share to score any association (default 2)")
    parser.add_argument("--min-pmi", type=float, default=1.0, help="average PMI required to merge two clusters (default 1.0)")
    parser.add_argument("--min-theme-docs", type=int, default=3, help="documents a cluster needs to be reported as a theme (default 3)")
    parser.add_argument("--top", type=int, default=8, help="themes to detail (default 8)")
    parser.add_argument("--examples", type=int, default=2, help="verbatims to quote per theme (default 2)")
    parser.add_argument("--quote-chars", type=int, default=150, help="characters of each verbatim to quote (default 150)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if args.min_term_docs < 1 or args.min_pair_docs < 1 or args.min_theme_docs < 1:
        fail("--min-term-docs, --min-pair-docs, and --min-theme-docs must be 1 or greater")
    if not (ZERO < args.max_term_df_pct <= Decimal("100")):
        fail("--max-term-df-pct must be above 0 and at most 100")
    if args.top < 1 or args.examples < 1 or args.quote_chars < 20:
        fail("--top and --examples must be 1 or greater, and --quote-chars at least 20")

    documents = read_feedback(args.feedback)
    total_docs = len(documents)

    vocabulary = build_vocabulary(documents, args.min_term_docs, args.max_term_df_pct)
    if not vocabulary:
        fail(
            f"{args.feedback}: no term appears in at least {args.min_term_docs} documents and under "
            f"{args.max_term_df_pct}% of them; lower --min-term-docs or supply more feedback"
        )

    pairs = co_occurrence(documents, vocabulary)
    table = pmi_table(pairs, vocabulary, total_docs, args.min_pair_docs)
    clusters = cluster_terms(vocabulary, table, args.min_pmi)
    assign_documents(documents, clusters, vocabulary, total_docs)

    themes: list[Theme] = []
    for index, terms in enumerate(clusters):
        docs = [document for document in documents if document.theme == index]
        if len(docs) < args.min_theme_docs:
            for document in docs:
                document.theme = None
            continue
        ordered_terms = sorted(terms, key=lambda term: (-vocabulary[term], term))
        theme = Theme(index=index, terms=ordered_terms, docs=docs)
        classify(theme)
        themes.append(theme)

    overall = mean_rating(documents)
    unclustered = [document for document in documents if document.theme is None]

    def drag(theme: Theme) -> Decimal:
        theme_mean = mean_rating(theme.docs)
        if theme_mean is None or overall is None:
            return ZERO
        rated = sum(1 for document in theme.docs if document.rating is not None)
        return (overall - theme_mean) * Decimal(rated)

    themes.sort(key=lambda theme: (-drag(theme), -len(theme.docs), theme.label))

    if args.json:
        print(render_json(args, documents, themes, unclustered, overall, drag))
        return 0
    print(render_text(args, documents, themes, unclustered, overall, vocabulary, drag))
    return 0


def render_text(
    args: argparse.Namespace,
    documents: list[Document],
    themes: list[Theme],
    unclustered: list[Document],
    overall: Decimal | None,
    vocabulary: "OrderedDict[str, int]",
    drag,
) -> str:
    lines: list[str] = []
    reviews = [document for document in documents if document.source == "review"]
    tickets = [document for document in documents if document.source == "ticket"]
    rated = [document for document in reviews if document.rating is not None]

    lines.append("REVIEW AND TICKET THEME MINER")
    lines.append(
        f"documents: {len(documents)}   reviews: {len(reviews)}   tickets: {len(tickets)}   "
        f"rated reviews: {len(rated)}   mean rating: {quant(overall)}"
    )
    lines.append("=" * 104)
    lines.append("")

    lines.append("METHOD")
    lines.append(
        f"  Vocabulary: terms in at least {args.min_term_docs} documents and at most "
        f"{args.max_term_df_pct}% of them ({len(vocabulary)} terms kept)."
    )
    lines.append(
        f"  Themes: terms merged by average pointwise mutual information at or above {args.min_pmi} bits, "
        f"then every document assigned to its best-matching cluster by inverse document frequency."
    )
    lines.append(
        "  Categories come from a signal lexicon applied AFTER clustering; they label a theme, they never create one."
    )
    lines.append(
        "  Rating drag = (overall mean rating - theme mean rating) x rated reviews in the theme, in star-points."
    )
    lines.append(
        "  A negative drag means the theme rates ABOVE the overall mean: those clusters are praise, not a problem."
    )
    lines.append("")

    lines.append("THEMES, RANKED BY RATING DRAG")
    lines.append(
        f"  {'#':<3}{'theme':<34}{'docs':>6}{'share':>8}{'revs':>6}{'tix':>5}{'rating':>8}{'drag':>8}  category"
    )
    for position, theme in enumerate(themes, start=1):
        theme_reviews = sum(1 for document in theme.docs if document.source == "review")
        theme_tickets = len(theme.docs) - theme_reviews
        share = (Decimal(len(theme.docs)) / Decimal(len(documents))) * Decimal(100)
        lines.append(
            f"  {position:<3}{theme.label[:33]:<34}{len(theme.docs):>6}"
            f"{quant(share, '0.1') + '%':>8}{theme_reviews:>6}{theme_tickets:>5}"
            f"{quant(mean_rating(theme.docs)):>8}{quant(drag(theme), '0.1'):>8}  {theme.category}"
        )
    if unclustered:
        share = (Decimal(len(unclustered)) / Decimal(len(documents))) * Decimal(100)
        lines.append(
            f"  {'-':<3}{'(no recurring theme)':<34}{len(unclustered):>6}{quant(share, '0.1') + '%':>8}"
            f"{sum(1 for d in unclustered if d.source == 'review'):>6}"
            f"{sum(1 for d in unclustered if d.source == 'ticket'):>5}"
            f"{quant(mean_rating(unclustered)):>8}{'-':>8}  one-off feedback"
        )
    lines.append("")

    lines.append("THEME DETAIL")
    for position, theme in enumerate(themes[: args.top], start=1):
        owner, action = ACTIONS[theme.category]
        one_two = sum(1 for document in theme.docs if document.rating is not None and document.rating <= 2)
        lines.append("")
        lines.append(f"  [{position}] {theme.label}  —  {theme.category}")
        lines.append(
            f"      terms: {', '.join(theme.display_terms[:10])}"
            + (" ..." if len(theme.terms) > 10 else "")
        )
        lines.append(
            f"      volume: {len(theme.docs)} documents, {one_two} reviews at 1-2 stars, "
            f"mean rating {quant(mean_rating(theme.docs))}, drag {quant(drag(theme), '0.1')} star-points"
        )
        signals = ", ".join(
            f"{name.lower()} {count}" for name, count in theme.signal_counts.items() if count
        )
        lines.append(f"      signal counts: {signals or 'none'}")
        skus = sorted({document.sku for document in theme.docs if document.sku})
        if skus:
            counts = OrderedDict(
                (sku, sum(1 for document in theme.docs if document.sku == sku)) for sku in skus
            )
            ranked_skus = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
            lines.append(
                "      skus: " + ", ".join(f"{sku} ({count})" for sku, count in ranked_skus[:5])
            )
        for document in representative(theme, args.examples):
            quote = document.text[: args.quote_chars]
            if len(document.text) > args.quote_chars:
                quote += "..."
            star = f"{document.rating}*" if document.rating is not None else document.source
            lines.append(f"      \"{quote}\" [{document.doc_id}, {star}]")
        lines.append(f"      owner: {owner}")
        lines.append(f"      action: {action}")
    if len(themes) > args.top:
        lines.append("")
        lines.append(f"  ... {len(themes) - args.top} further theme(s); raise --top to detail them.")
    lines.append("")

    lines.append("DEFECT VERSUS EXPECTATION")
    defect_docs = sum(len(theme.docs) for theme in themes if theme.category == "PRODUCT DEFECT")
    expectation_docs = sum(len(theme.docs) for theme in themes if theme.category == "EXPECTATION MISMATCH")
    other_docs = sum(
        len(theme.docs)
        for theme in themes
        if theme.category not in ("PRODUCT DEFECT", "EXPECTATION MISMATCH")
    )
    lines.append(f"  product defects (the item is failing):        {defect_docs} document(s)")
    lines.append(f"  expectation mismatch (the page is failing):   {expectation_docs} document(s)")
    lines.append(f"  logistics, service, usability, value:         {other_docs} document(s)")
    lines.append(f"  no recurring theme:                           {len(unclustered)} document(s)")
    lines.append(
        "  Defect themes cost margin through replacements and returns; expectation themes are usually "
        "cheaper to fix and fix faster, because the change is copy, spec, and photography."
    )
    lines.append("")
    lines.append("=" * 104)
    lines.append(
        "Themes are statistical groupings of shared vocabulary, not a reading of intent. Sentiment is not "
        "modelled: a theme mixes praise and complaint about the same subject, so read the verbatims before acting."
    )
    return "\n".join(lines)


def render_json(
    args: argparse.Namespace,
    documents: list[Document],
    themes: list[Theme],
    unclustered: list[Document],
    overall: Decimal | None,
    drag,
) -> str:
    payload = {
        "documentCount": len(documents),
        "reviewCount": sum(1 for document in documents if document.source == "review"),
        "ticketCount": sum(1 for document in documents if document.source == "ticket"),
        "meanRating": quant(overall),
        "themes": [
            {
                "label": theme.label,
                "category": theme.category,
                "terms": theme.display_terms,
                "documentCount": len(theme.docs),
                "reviewCount": sum(1 for document in theme.docs if document.source == "review"),
                "ticketCount": sum(1 for document in theme.docs if document.source == "ticket"),
                "lowStarReviews": sum(
                    1 for document in theme.docs if document.rating is not None and document.rating <= 2
                ),
                "meanRating": quant(mean_rating(theme.docs)),
                "ratingDragStarPoints": quant(drag(theme), "0.1"),
                "signalCounts": dict(theme.signal_counts),
                "skus": sorted({document.sku for document in theme.docs if document.sku}),
                "documentIds": [document.doc_id for document in theme.docs],
                "examples": [
                    {"id": document.doc_id, "rating": document.rating, "text": document.text}
                    for document in representative(theme, args.examples)
                ],
                "owner": ACTIONS[theme.category][0],
                "action": ACTIONS[theme.category][1],
            }
            for theme in themes
        ],
        "unclusteredDocumentIds": [document.doc_id for document in unclustered],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


if __name__ == "__main__":
    sys.exit(main())
