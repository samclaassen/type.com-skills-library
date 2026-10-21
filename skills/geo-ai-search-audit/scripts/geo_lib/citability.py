"""Passage-level "citability" heuristic.

``score_passage`` is ported from citability_scorer.py in
zubair-trabzada/geo-seo-claude (MIT) with two changes: the year pattern
matches 2010-2029 instead of a hard-coded 2023-2026 window, and the word-count
thresholds are exposed as constants. The scores are heuristics about how easy
a passage is to quote; they are not measurements of any AI engine.
"""

from __future__ import annotations

import re

OPTIMAL_MIN_WORDS = 134
OPTIMAL_MAX_WORDS = 167
MIN_BLOCK_WORDS = 20


def score_passage(text: str, heading: str | None = None) -> dict:
    """Score a single passage for AI citability (0-100)."""
    words = text.split()
    word_count = len(words)

    scores = {
        "answer_block_quality": 0,
        "self_containment": 0,
        "structural_readability": 0,
        "statistical_density": 0,
        "uniqueness_signals": 0,
    }

    # 1. Answer block quality (max 30)
    abq = 0
    definition_patterns = [
        r"\b\w+\s+is\s+(?:a|an|the)\s",
        r"\b\w+\s+refers?\s+to\s",
        r"\b\w+\s+means?\s",
        r"\b\w+\s+(?:can be |are )?defined\s+as\s",
        r"\bin\s+(?:simple|other)\s+(?:terms|words)\s*,",
    ]
    if any(re.search(p, text, re.IGNORECASE) for p in definition_patterns):
        abq += 15
    first_60 = " ".join(words[:60])
    if any(
        re.search(p, first_60, re.IGNORECASE)
        for p in [r"\b(?:is|are|was|were|means?|refers?)\b", r"\d+%", r"\$[\d,]+",
                  r"\d+\s+(?:million|billion|thousand)"]
    ):
        abq += 15
    if heading and heading.endswith("?"):
        abq += 10
    sentences = re.split(r"[.!?]+", text)
    short_clear = sum(1 for s in sentences if 5 <= len(s.split()) <= 25)
    if sentences:
        abq += int(short_clear / len(sentences) * 10)
    if re.search(
        r"(?:according to|research shows|studies? (?:show|indicate|suggest|found)|data (?:shows|indicates|suggests))",
        text, re.IGNORECASE,
    ):
        abq += 10
    scores["answer_block_quality"] = min(abq, 30)

    # 2. Self-containment (max 25)
    sc = 0
    if OPTIMAL_MIN_WORDS <= word_count <= OPTIMAL_MAX_WORDS:
        sc += 10
    elif 100 <= word_count <= 200:
        sc += 7
    elif 80 <= word_count <= 250:
        sc += 4
    elif word_count < 30 or word_count > 400:
        sc += 0
    else:
        sc += 2
    pronouns = len(re.findall(r"\b(?:it|they|them|their|this|that|these|those|he|she|his|her)\b", text, re.IGNORECASE))
    if word_count:
        ratio = pronouns / word_count
        if ratio < 0.02:
            sc += 8
        elif ratio < 0.04:
            sc += 5
        elif ratio < 0.06:
            sc += 3
    proper_nouns = len(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text))
    if proper_nouns >= 3:
        sc += 7
    elif proper_nouns >= 1:
        sc += 4
    scores["self_containment"] = min(sc, 25)

    # 3. Structural readability (max 20)
    sr = 0
    if sentences:
        avg = word_count / len(sentences)
        sr += 8 if 10 <= avg <= 20 else 5 if 8 <= avg <= 25 else 2
    if re.search(r"(?:first|second|third|finally|additionally|moreover|furthermore)", text, re.IGNORECASE):
        sr += 4
    if re.search(r"(?:\d+[\.\)]\s|\b(?:step|tip|point)\s+\d+)", text, re.IGNORECASE):
        sr += 4
    if "\n" in text:
        sr += 4
    scores["structural_readability"] = min(sr, 20)

    # 4. Statistical density (max 15)
    sd = 0
    sd += min(len(re.findall(r"\d+(?:\.\d+)?%", text)) * 3, 6)
    sd += min(len(re.findall(r"\$[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|M|B|K))?", text)) * 3, 5)
    sd += min(len(re.findall(
        r"\b\d+(?:,\d{3})*(?:\.\d+)?\s+(?:users|customers|pages|sites|companies|businesses|people|percent|times|x\b)",
        text, re.IGNORECASE)) * 2, 4)
    if re.search(r"\b20[12]\d\b", text):
        sd += 2
    for pattern in [
        r"(?:according to|per|from|by)\s+[A-Z]",
        r"(?:Gartner|Forrester|McKinsey|Harvard|Stanford|MIT|Google|Microsoft|OpenAI|Anthropic)",
        r"\([A-Z][a-z]+(?:\s+\d{4})?\)",
    ]:
        if re.search(pattern, text):
            sd += 2
    scores["statistical_density"] = min(sd, 15)

    # 5. Uniqueness signals (max 10)
    us = 0
    if re.search(r"(?:our (?:research|study|data|analysis|survey|findings)|we (?:found|discovered|analyzed|surveyed|measured))", text, re.IGNORECASE):
        us += 5
    if re.search(r"(?:case study|for example|for instance|in practice|real-world|hands-on)", text, re.IGNORECASE):
        us += 3
    if re.search(r"(?:using|with|via|through)\s+[A-Z][a-z]+", text):
        us += 2
    scores["uniqueness_signals"] = min(us, 10)

    total = sum(scores.values())
    if total >= 80:
        grade, label = "A", "Highly citable"
    elif total >= 65:
        grade, label = "B", "Good citability"
    elif total >= 50:
        grade, label = "C", "Moderate citability"
    elif total >= 35:
        grade, label = "D", "Low citability"
    else:
        grade, label = "F", "Poor citability"

    return {
        "heading": heading,
        "word_count": word_count,
        "total_score": total,
        "grade": grade,
        "label": label,
        "breakdown": scores,
        "preview": " ".join(words[:30]) + ("..." if word_count > 30 else ""),
    }


def analyze_blocks(pages: list) -> dict:
    """Score every heading-delimited block (>= MIN_BLOCK_WORDS words) across pages."""
    scored = []
    for page in pages:
        for block in page.blocks:
            if len(block["content"].split()) < MIN_BLOCK_WORDS:
                continue
            result = score_passage(block["content"], block["heading"])
            result["page"] = page.url
            scored.append(result)
    grades = {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0}
    for block in scored:
        grades[block["grade"]] += 1
    average = round(sum(b["total_score"] for b in scored) / len(scored), 1) if scored else 0.0
    ranked = sorted(scored, key=lambda b: (-b["total_score"], b["page"], b["heading"] or ""))
    return {
        "blocks_analyzed": len(scored),
        "average_score": average,
        "optimal_length_blocks": sum(
            1 for b in scored if OPTIMAL_MIN_WORDS <= b["word_count"] <= OPTIMAL_MAX_WORDS
        ),
        "grade_distribution": grades,
        "strongest": ranked[:3],
        "weakest": list(reversed(ranked[-3:])) if len(ranked) > 3 else [],
    }
