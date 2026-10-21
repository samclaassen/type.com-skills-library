#!/usr/bin/env python3
"""Match RFP requirements against an answer library and report coverage.

Stdlib only, offline, deterministic. Every "today" comes from --as-of.

Each requirement is classified mandatory / weighted / optional, tokenized, and
scored against every answer by IDF-weighted term coverage:

    coverage = sum(idf of requirement terms found in the answer)
               / sum(idf of all requirement terms)
    score    = min(1.0, coverage + tag bonus)

Terms that appear nowhere in the library carry the maximum IDF, so a
requirement full of unknown vocabulary scores near zero rather than matching
whatever answer happens to share the word "data".

Exit codes: 0 ok, 2 bad input (message names the file, row and column).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR.parent / "config" / "matching.json"

REQUIREMENT_COLUMNS = ["requirement_id", "section", "requirement_text", "classification", "weight"]
ANSWER_COLUMNS = [
    "answer_id",
    "title",
    "answer_text",
    "tags",
    "owner",
    "product_area",
    "last_reviewed_date",
]
CLASSIFICATIONS = ("mandatory", "weighted", "optional")
TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


class RfpError(Exception):
    """Raised for any unusable input; the message names the offending field."""


@dataclass
class Answer:
    answer_id: str
    title: str
    text: str
    tags: list[str]
    owner: str
    product_area: str
    last_reviewed: date
    tokens: set[str] = field(default_factory=set)
    tag_tokens: set[str] = field(default_factory=set)


@dataclass
class Requirement:
    row_no: int
    requirement_id: str
    section: str
    text: str
    classification: str
    inferred: bool
    weight: float
    tokens: list[str] = field(default_factory=list)


def read_rows(path: Path, required: list[str], label: str) -> list[tuple[dict[str, str], int]]:
    if not path.is_file():
        raise RfpError(f"{label}: file not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RfpError(
                f"{label}: {path} is empty; expected a header row with: {', '.join(required)}"
            )
        header = [(name or "").strip() for name in reader.fieldnames]
        missing = [name for name in required if name not in header]
        if missing:
            raise RfpError(f"{label}: {path} is missing required column(s): {', '.join(missing)}")
        rows = [
            (
                {
                    (key or "").strip(): (value or "").strip()
                    for key, value in raw.items()
                    if key is not None
                },
                row_no,
            )
            for row_no, raw in enumerate(reader, start=2)
        ]
    if not rows:
        raise RfpError(f"{label}: {path} has a header but no data rows")
    return rows


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise RfpError(f"config: file not found: {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RfpError(f"config: {path} is not valid JSON ({exc})") from None
    required = [
        "min_token_length",
        "strong_match_threshold",
        "partial_match_threshold",
        "tag_bonus_per_match",
        "tag_bonus_cap",
        "max_matches_per_requirement",
        "stale_after_days",
        "default_weight",
        "inference",
        "stopwords",
    ]
    missing = [key for key in required if key not in config]
    if missing:
        raise RfpError(f"config: {path} is missing key(s): {', '.join(missing)}")
    if not 0 < float(config["partial_match_threshold"]) <= float(config["strong_match_threshold"]) <= 1:
        raise RfpError(
            "config: thresholds must satisfy 0 < partial_match_threshold <="
            " strong_match_threshold <= 1"
        )
    return config


def normalize(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if (
        len(token) > 4
        and token.endswith("s")
        and not token.endswith("ss")
        and not token.endswith("us")
    ):
        return token[:-1]
    return token


def tokenize(text: str, config: dict) -> list[str]:
    stopwords = {word.lower() for word in config["stopwords"]}
    minimum = int(config["min_token_length"])
    seen: list[str] = []
    for raw in TOKEN_SPLIT.split(text.lower()):
        if not raw or raw in stopwords or len(raw) < minimum:
            continue
        token = normalize(raw)
        if token in stopwords or len(token) < minimum:
            continue
        if token not in seen:
            seen.append(token)
    return seen


def load_answers(path: Path, config: dict) -> list[Answer]:
    rows = read_rows(path, ANSWER_COLUMNS, "answers")
    answers: list[Answer] = []
    seen: dict[str, int] = {}
    for row, row_no in rows:
        answer_id = row["answer_id"]
        if not answer_id:
            raise RfpError(f"answers row {row_no}: column 'answer_id' is required but empty")
        if answer_id in seen:
            raise RfpError(
                f"answers row {row_no}: answer_id {answer_id!r} already appeared on row {seen[answer_id]}"
            )
        seen[answer_id] = row_no
        if not row["answer_text"]:
            raise RfpError(f"answers row {row_no}: column 'answer_text' is required but empty")
        try:
            last_reviewed = datetime.strptime(row["last_reviewed_date"], "%Y-%m-%d").date()
        except ValueError:
            raise RfpError(
                f"answers row {row_no}: column 'last_reviewed_date' value"
                f" {row['last_reviewed_date']!r} is not a YYYY-MM-DD date"
            ) from None
        tags = [tag.strip() for tag in row["tags"].split(";") if tag.strip()]
        answer = Answer(
            answer_id=answer_id,
            title=row["title"],
            text=row["answer_text"],
            tags=tags,
            owner=row["owner"] or "(unowned)",
            product_area=row["product_area"],
            last_reviewed=last_reviewed,
        )
        answer.tokens = set(
            tokenize(" ".join([answer.title, answer.text, " ".join(tags), answer.product_area]), config)
        )
        answer.tag_tokens = set(tokenize(" ".join(tags), config))
        answers.append(answer)
    return answers


def infer_classification(text: str, config: dict) -> str | None:
    lowered = text.lower()
    for keyword in config["inference"]["mandatory_keywords"]:
        if keyword.lower() in lowered:
            return "mandatory"
    for keyword in config["inference"]["weighted_keywords"]:
        if keyword.lower() in lowered:
            return "weighted"
    return None


def load_requirements(path: Path, config: dict, allow_inference: bool) -> list[Requirement]:
    rows = read_rows(path, REQUIREMENT_COLUMNS, "requirements")
    requirements: list[Requirement] = []
    seen: dict[str, int] = {}
    for row, row_no in rows:
        requirement_id = row["requirement_id"]
        if not requirement_id:
            raise RfpError(f"requirements row {row_no}: column 'requirement_id' is required but empty")
        if requirement_id in seen:
            raise RfpError(
                f"requirements row {row_no}: requirement_id {requirement_id!r}"
                f" already appeared on row {seen[requirement_id]}"
            )
        seen[requirement_id] = row_no
        text = row["requirement_text"]
        if not text:
            raise RfpError(
                f"requirements row {row_no}: column 'requirement_text' is required but empty"
            )
        classification = row["classification"].lower()
        inferred = False
        if not classification:
            if not allow_inference:
                raise RfpError(
                    f"requirements row {row_no}: column 'classification' is empty;"
                    " supply mandatory/weighted/optional or pass --infer-classification"
                )
            classification = infer_classification(text, config) or "optional"
            inferred = True
        if classification not in CLASSIFICATIONS:
            raise RfpError(
                f"requirements row {row_no}: column 'classification' value"
                f" {row['classification']!r} must be one of {', '.join(CLASSIFICATIONS)}"
            )
        weight_raw = row["weight"]
        if weight_raw:
            try:
                weight = float(weight_raw)
            except ValueError:
                raise RfpError(
                    f"requirements row {row_no}: column 'weight' value {weight_raw!r} is not a number"
                ) from None
            if weight < 0:
                raise RfpError(
                    f"requirements row {row_no}: column 'weight' value {weight_raw!r} is negative"
                )
        elif classification == "weighted":
            if not inferred:
                raise RfpError(
                    f"requirements row {row_no}: column 'weight' is required for"
                    " weighted requirements"
                )
            weight = float(config["default_weight"])
        else:
            weight = 0.0
        requirement = Requirement(
            row_no=row_no,
            requirement_id=requirement_id,
            section=row["section"] or "(no section)",
            text=text,
            classification=classification,
            inferred=inferred,
            weight=weight,
        )
        requirement.tokens = tokenize(text, config)
        if not requirement.tokens:
            raise RfpError(
                f"requirements row {row_no}: 'requirement_text' has no scoreable terms after"
                " stopword removal; shorten the stopword list or rewrite the requirement"
            )
        requirements.append(requirement)
    return requirements


def build_idf(answers: list[Answer]) -> tuple[dict[str, float], float]:
    total = len(answers)
    document_frequency: dict[str, int] = {}
    for answer in answers:
        for token in answer.tokens:
            document_frequency[token] = document_frequency.get(token, 0) + 1
    idf = {
        token: math.log((total + 1) / (count + 1)) + 1.0
        for token, count in document_frequency.items()
    }
    unknown_idf = math.log(total + 1) + 1.0
    return idf, unknown_idf


def score_requirement(
    requirement: Requirement,
    answers: list[Answer],
    idf: dict[str, float],
    unknown_idf: float,
    config: dict,
) -> list[dict]:
    weights = {token: idf.get(token, unknown_idf) for token in requirement.tokens}
    denominator = sum(weights.values())
    if denominator <= 0:
        raise RfpError(
            f"requirement {requirement.requirement_id}: total term weight is zero;"
            " this indicates an empty stopword-filtered requirement"
        )
    tag_bonus_unit = float(config["tag_bonus_per_match"])
    tag_bonus_cap = float(config["tag_bonus_cap"])

    scored: list[dict] = []
    for answer in answers:
        matched = [token for token in requirement.tokens if token in answer.tokens]
        missing = [token for token in requirement.tokens if token not in answer.tokens]
        coverage = sum(weights[token] for token in matched) / denominator
        tag_hits = [token for token in requirement.tokens if token in answer.tag_tokens]
        tag_bonus = min(len(tag_hits) * tag_bonus_unit, tag_bonus_cap)
        score = min(1.0, coverage + tag_bonus)
        scored.append(
            {
                "answer": answer,
                "coverage": round(coverage, 4),
                "tag_bonus": round(tag_bonus, 4),
                "tag_hits": tag_hits,
                "score": round(score, 4),
                "matched": sorted(matched, key=lambda token: (-weights[token], token)),
                "missing": sorted(missing, key=lambda token: (-weights[token], token)),
            }
        )
    scored.sort(key=lambda item: (-item["score"], item["answer"].answer_id))
    return scored


def evaluate(
    requirements: list[Requirement], answers: list[Answer], config: dict, as_of: date
) -> list[dict]:
    idf, unknown_idf = build_idf(answers)
    strong = float(config["strong_match_threshold"])
    partial = float(config["partial_match_threshold"])
    stale_days = int(config["stale_after_days"])
    limit = int(config["max_matches_per_requirement"])

    results: list[dict] = []
    for requirement in requirements:
        scored = score_requirement(requirement, answers, idf, unknown_idf, config)
        best = scored[0]
        if best["score"] >= strong:
            status = "covered"
        elif best["score"] >= partial:
            status = "partial"
        else:
            status = "gap"
        matches = []
        for item in scored[:limit]:
            answer = item["answer"]
            age = (as_of - answer.last_reviewed).days
            matches.append(
                {
                    **item,
                    "age_days": age,
                    "stale": age > stale_days,
                }
            )
        results.append(
            {
                "requirement": requirement,
                "status": status,
                "best_score": best["score"],
                "matches": matches,
            }
        )
    return results


def summarize(results: list[dict], answers: list[Answer], config: dict, as_of: date) -> dict:
    by_classification: dict[str, dict[str, int]] = {
        name: {"covered": 0, "partial": 0, "gap": 0} for name in CLASSIFICATIONS
    }
    for item in results:
        by_classification[item["requirement"].classification][item["status"]] += 1

    weighted = [item for item in results if item["requirement"].classification == "weighted"]
    weight_total = sum(item["requirement"].weight for item in weighted)
    if weight_total > 0:
        weighted_coverage = sum(
            item["requirement"].weight * item["best_score"] for item in weighted
        ) / weight_total
    else:
        weighted_coverage = None

    used_ids = {
        item["matches"][0]["answer"].answer_id
        for item in results
        if item["matches"] and item["status"] != "gap"
    }
    unused = [answer for answer in answers if answer.answer_id not in used_ids]
    stale_days = int(config["stale_after_days"])
    stale_used = sorted(
        {
            match["answer"].answer_id
            for item in results
            for match in item["matches"][:1]
            if item["status"] != "gap" and match["stale"]
        }
    )

    sections: dict[str, dict] = {}
    for item in results:
        section = item["requirement"].section
        bucket = sections.setdefault(
            section, {"count": 0, "score_sum": 0.0, "gaps": 0, "mandatory_gaps": 0}
        )
        bucket["count"] += 1
        bucket["score_sum"] += item["best_score"]
        if item["status"] == "gap":
            bucket["gaps"] += 1
            if item["requirement"].classification == "mandatory":
                bucket["mandatory_gaps"] += 1

    return {
        "by_classification": by_classification,
        "weighted_coverage": weighted_coverage,
        "weight_total": weight_total,
        "unused_answers": unused,
        "stale_used": stale_used,
        "sections": sections,
        "library_size": len(answers),
        "as_of": as_of,
    }


def render_text(
    results: list[dict], summary: dict, config: dict, as_of: date, paths: dict[str, str]
) -> str:
    lines: list[str] = []
    out = lines.append
    strong = float(config["strong_match_threshold"])
    partial = float(config["partial_match_threshold"])

    out(f"RFP RESPONSE COVERAGE — as of {as_of.isoformat()}")
    out(f"requirements: {paths['requirements']}   answers: {paths['answers']}")
    out(f"matching config: {paths['config']}")
    out(
        f"thresholds: covered >= {strong:.2f}, partial >= {partial:.2f};"
        " score = IDF-weighted term coverage + tag bonus"
    )
    out("")

    total = len(results)
    out("COVERAGE SUMMARY")
    out(f"  Requirements parsed        {total}")
    for name in CLASSIFICATIONS:
        bucket = summary["by_classification"][name]
        count = sum(bucket.values())
        if count == 0:
            continue
        out(
            f"  {name:<12}{count:>3}   covered {bucket['covered']:>3}"
            f"   partial {bucket['partial']:>3}   gap {bucket['gap']:>3}"
        )
    if summary["weighted_coverage"] is None:
        out("  Weighted score             n/a (no weighted requirements)")
    else:
        out(
            f"  Weighted score             {summary['weighted_coverage'] * 100:.1f}% of"
            f" {summary['weight_total']:g} available points"
        )
    out(f"  Answer library             {summary['library_size']} answers,"
        f" {len(summary['unused_answers'])} never used as a best match")
    out("")

    blockers = [
        item
        for item in results
        if item["requirement"].classification == "mandatory" and item["status"] == "gap"
    ]
    out(f"BLOCKERS — mandatory requirements nobody can answer ({len(blockers)})")
    if not blockers:
        out("  None. Every mandatory requirement has at least a partial answer.")
    for item in sorted(
        blockers, key=lambda entry: (entry["best_score"], entry["requirement"].requirement_id)
    ):
        requirement = item["requirement"]
        out(f"  {requirement.requirement_id}  [{requirement.section}]  best score {item['best_score']:.2f}")
        out(f"      {requirement.text}")
        if item["matches"]:
            best = item["matches"][0]
            missing = ", ".join(best["missing"][:6]) or "(none)"
            out(f"      closest: {best['answer'].answer_id} {best['answer'].title} ({best['score']:.2f})")
            out(f"      unanswered terms: {missing}")
        out("      action: assign an SME to write a new library answer before submitting.")
    out("")

    weighted_gaps = [
        item
        for item in results
        if item["requirement"].classification == "weighted" and item["status"] != "covered"
    ]
    out(f"WEIGHTED POINTS AT RISK ({len(weighted_gaps)})")
    if not weighted_gaps:
        out("  None. Every weighted requirement has a strong answer.")
    for item in sorted(
        weighted_gaps,
        key=lambda entry: (
            -(entry["requirement"].weight * (1 - entry["best_score"])),
            entry["requirement"].requirement_id,
        ),
    ):
        requirement = item["requirement"]
        at_risk = requirement.weight * (1 - item["best_score"])
        out(
            f"  {requirement.requirement_id}  [{requirement.section}]"
            f"  weight {requirement.weight:g}  best {item['best_score']:.2f}"
            f"  -> {at_risk:.2f} points at risk ({item['status']})"
        )
        out(f"      {requirement.text}")
    out("")

    out("SECTION COVERAGE")
    out(f"  {'section':<26}{'reqs':>5}{'avg best':>10}{'gaps':>7}{'mandatory gaps':>16}")
    for section in sorted(summary["sections"]):
        bucket = summary["sections"][section]
        average = bucket["score_sum"] / bucket["count"] if bucket["count"] else 0.0
        out(
            f"  {section:<26}{bucket['count']:>5}{average:>10.2f}{bucket['gaps']:>7}"
            f"{bucket['mandatory_gaps']:>16}"
        )
    out("")

    out("REQUIREMENT DETAIL")
    for item in sorted(
        results, key=lambda entry: (entry["requirement"].section, entry["requirement"].requirement_id)
    ):
        requirement = item["requirement"]
        classification = requirement.classification + (" (inferred)" if requirement.inferred else "")
        weight_note = f", weight {requirement.weight:g}" if requirement.classification == "weighted" else ""
        out("")
        out(
            f"  {requirement.requirement_id}  [{requirement.section}]  {classification}{weight_note}"
            f"  status: {item['status']} ({item['best_score']:.2f})"
        )
        out(f"      {requirement.text}")
        if item["status"] == "gap":
            out("      no answer clears the partial threshold; treat the matches below as research leads.")
        for rank, match in enumerate(item["matches"], start=1):
            answer = match["answer"]
            stale_note = f" STALE ({match['age_days']}d since review)" if match["stale"] else ""
            out(
                f"      {rank}. {match['score']:.2f}  {answer.answer_id}  {answer.title}"
                f"  owner: {answer.owner}{stale_note}"
            )
            matched = ", ".join(match["matched"][:6]) or "(no shared terms)"
            missing = ", ".join(match["missing"][:6]) or "(none)"
            out(f"         matched terms: {matched}")
            out(f"         missing terms: {missing}")
            if match["tag_hits"]:
                out(
                    f"         tag bonus +{match['tag_bonus']:.2f} from tags:"
                    f" {', '.join(match['tag_hits'])}"
                )
    out("")

    out("ANSWER LIBRARY HEALTH")
    if summary["stale_used"]:
        out(
            f"  {len(summary['stale_used'])} best-match answer(s) past the"
            f" {int(config['stale_after_days'])}-day review window: {', '.join(summary['stale_used'])}"
        )
    else:
        out("  No stale answers among the selected best matches.")
    if summary["unused_answers"]:
        out(
            f"  {len(summary['unused_answers'])} answer(s) never selected: "
            + ", ".join(answer.answer_id for answer in summary["unused_answers"])
        )
    else:
        out("  Every library answer was selected at least once.")
    return "\n".join(lines) + "\n"


def render_json(
    results: list[dict], summary: dict, config: dict, as_of: date, paths: dict[str, str]
) -> str:
    payload = {
        "as_of": as_of.isoformat(),
        "inputs": paths,
        "thresholds": {
            "strong": float(config["strong_match_threshold"]),
            "partial": float(config["partial_match_threshold"]),
        },
        "summary": {
            "requirements": len(results),
            "by_classification": summary["by_classification"],
            "weighted_coverage": summary["weighted_coverage"],
            "weight_total": summary["weight_total"],
            "library_size": summary["library_size"],
            "unused_answer_ids": [answer.answer_id for answer in summary["unused_answers"]],
            "stale_best_match_answer_ids": summary["stale_used"],
        },
        "sections": {
            section: {
                "requirements": bucket["count"],
                "average_best_score": round(bucket["score_sum"] / bucket["count"], 4)
                if bucket["count"]
                else 0.0,
                "gaps": bucket["gaps"],
                "mandatory_gaps": bucket["mandatory_gaps"],
            }
            for section, bucket in sorted(summary["sections"].items())
        },
        "requirements": [
            {
                "requirement_id": item["requirement"].requirement_id,
                "section": item["requirement"].section,
                "classification": item["requirement"].classification,
                "classification_inferred": item["requirement"].inferred,
                "weight": item["requirement"].weight,
                "text": item["requirement"].text,
                "status": item["status"],
                "best_score": item["best_score"],
                "matches": [
                    {
                        "answer_id": match["answer"].answer_id,
                        "title": match["answer"].title,
                        "owner": match["answer"].owner,
                        "score": match["score"],
                        "coverage": match["coverage"],
                        "tag_bonus": match["tag_bonus"],
                        "matched_terms": match["matched"],
                        "missing_terms": match["missing"],
                        "age_days": match["age_days"],
                        "stale": match["stale"],
                    }
                    for match in item["matches"]
                ],
            }
            for item in sorted(
                results,
                key=lambda entry: (
                    entry["requirement"].section,
                    entry["requirement"].requirement_id,
                ),
            )
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Match RFP requirements against an answer library and report coverage."
    )
    parser.add_argument("--requirements", required=True, help="CSV export of RFP requirements")
    parser.add_argument("--answers", required=True, help="CSV export of the answer library")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="matching config JSON")
    parser.add_argument("--as-of", required=True, help="review date, YYYY-MM-DD")
    parser.add_argument(
        "--infer-classification",
        action="store_true",
        help="infer mandatory/weighted/optional from must/should language when the column is blank",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)

    try:
        try:
            as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date()
        except ValueError:
            raise RfpError(f"--as-of value {args.as_of!r} is not a YYYY-MM-DD date") from None
        config = load_config(Path(args.config))
        answers = load_answers(Path(args.answers), config)
        requirements = load_requirements(Path(args.requirements), config, args.infer_classification)
        results = evaluate(requirements, answers, config, as_of)
        summary = summarize(results, answers, config, as_of)
    except RfpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    paths = {
        "requirements": args.requirements,
        "answers": args.answers,
        "config": args.config,
    }
    sys.stdout.write(
        render_json(results, summary, config, as_of, paths)
        if args.json
        else render_text(results, summary, config, as_of, paths)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
