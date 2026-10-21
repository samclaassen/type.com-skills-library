#!/usr/bin/env python3
"""Mechanical brand-voice checks for one client (stdlib only, no network).

Reads the fenced ```voice-rules block in clients/<slug>/brand-voice.md and
prints every mechanical rule violation in a draft, with line numbers. The
format is defined in DATA_CONTRACT.md. Judgement (voice, tone, messaging,
claims) is the model's job; this script only catches the mechanical misses.

Usage:
    check_voice_rules.py --client SLUG --draft FILE [--clients-dir DIR]
    check_voice_rules.py --list [--clients-dir DIR]

Exit codes:
    0  no violations (or --list succeeded)
    1  violations found
    2  malformed input or usage error (message on stderr)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FENCE_OPEN = re.compile(r"^```voice-rules\s*$")
FENCE_CLOSE = re.compile(r"^```\s*$")
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
SENTENCE_END = re.compile(r"[.!?]+[\"')\]”’]*(?=\s|$)")
BLOCK_LINE = re.compile(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s)")
KNOWN_KEYS = {"client", "updated", "status", "ban", "prefer", "max_sentence_words", "exclamations"}
SINGLE_KEYS = {"client", "updated", "status", "max_sentence_words", "exclamations"}
STATUSES = {"draft", "approved"}


class InputError(Exception):
    """Malformed input: reported on stderr, exit 2."""


@dataclass
class Rules:
    client: str = ""
    updated: str = ""
    status: str = ""
    bans: list[str] = field(default_factory=list)
    prefers: list[tuple[str, str]] = field(default_factory=list)  # (preferred, avoided)
    max_sentence_words: int | None = None
    exclamations: str = "allow"  # "none", "allow", or a max count as digits


def read_text(path: Path, label: str) -> str:
    if not path.is_file():
        raise InputError(f"{label} not found: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise InputError(f"{label} is not valid UTF-8: {path}") from None


def load_client(clients_dir: Path, slug: str) -> dict:
    if not SLUG_PATTERN.match(slug):
        raise InputError(f"client slug {slug!r} is not lowercase letters, numbers and single hyphens")
    path = clients_dir / slug / "client.json"
    try:
        profile = json.loads(read_text(path, "client profile"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{path} is not valid JSON ({exc})") from None
    if not isinstance(profile, dict) or not isinstance(profile.get("name"), str) or not profile["name"].strip():
        raise InputError(f"{path} is missing the required \"name\" field")
    return profile


def parse_rules(text: str, source: Path) -> Rules:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if FENCE_OPEN.match(line)]
    if not starts:
        raise InputError(f"{source}: no ```voice-rules block found")
    if len(starts) > 1:
        raise InputError(f"{source}: {len(starts)} voice-rules blocks found; exactly one is allowed")
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if FENCE_CLOSE.match(lines[i])), None)
    if end is None:
        raise InputError(f"{source}: voice-rules block opened on line {start + 1} is never closed")

    rules = Rules()
    seen: set[str] = set()
    for number in range(start + 1, end):
        raw = lines[number].strip()
        where = f"{source}:{number + 1}"
        if not raw or raw.startswith("#"):
            continue
        key, sep, value = raw.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or key not in KNOWN_KEYS:
            raise InputError(f"{where}: expected one of {sorted(KNOWN_KEYS)} followed by ':', got {raw!r}")
        if key in SINGLE_KEYS:
            if key in seen:
                raise InputError(f"{where}: {key} may appear only once")
            seen.add(key)
        if not value:
            raise InputError(f"{where}: {key} has an empty value")

        if key == "client":
            if not SLUG_PATTERN.match(value):
                raise InputError(f"{where}: client {value!r} is not a valid slug")
            rules.client = value
        elif key == "updated":
            try:
                date.fromisoformat(value)
            except ValueError:
                raise InputError(f"{where}: updated must be YYYY-MM-DD, got {value!r}") from None
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise InputError(f"{where}: updated must be YYYY-MM-DD, got {value!r}")
            rules.updated = value
        elif key == "status":
            if value not in STATUSES:
                raise InputError(f"{where}: status must be one of {sorted(STATUSES)}, got {value!r}")
            rules.status = value
        elif key == "ban":
            rules.bans.append(value)
        elif key == "prefer":
            preferred, bar, avoided_list = value.partition("|")
            preferred = preferred.strip()
            avoided = [a.strip() for a in avoided_list.split(";")]
            if not bar or not preferred or not all(avoided):
                raise InputError(f"{where}: prefer must look like 'preferred | avoided[; avoided]', got {value!r}")
            for term in avoided:
                if term.lower() == preferred.lower():
                    raise InputError(f"{where}: {term!r} is both preferred and avoided")
                rules.prefers.append((preferred, term))
        elif key == "max_sentence_words":
            if not value.isdigit() or int(value) < 1:
                raise InputError(f"{where}: max_sentence_words must be a positive integer, got {value!r}")
            rules.max_sentence_words = int(value)
        elif key == "exclamations":
            if value in ("none", "allow"):
                rules.exclamations = value
            elif value.startswith("max ") and value[4:].strip().isdigit():
                rules.exclamations = value[4:].strip()
            else:
                raise InputError(f"{where}: exclamations must be 'none', 'allow' or 'max N', got {value!r}")

    for required in ("client", "updated", "status"):
        if required not in seen:
            raise InputError(f"{source}: voice-rules block is missing required key {required!r}")

    banned = {b.lower() for b in rules.bans}
    for preferred, _ in rules.prefers:
        if preferred.lower() in banned:
            raise InputError(f"{source}: {preferred!r} is both banned and preferred")
    return rules


def phrase_pattern(phrase: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in phrase.split())
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def sentences(lines: list[str]) -> list[tuple[int, str]]:
    """Return (start line, sentence text). Blank lines, headings and list items break sentences."""
    found: list[tuple[int, str]] = []
    buffer: list[str] = []
    start = 0

    def flush() -> None:
        nonlocal buffer
        text = " ".join(buffer).strip()
        if text:
            found.append((start, text))
        buffer = []

    for number, line in enumerate(lines, start=1):
        if not line.strip() or BLOCK_LINE.match(line):
            flush()
            if not line.strip():
                continue
        position = 0
        for match in SENTENCE_END.finditer(line):
            if not buffer and not line[position:match.end()].strip():
                position = match.end()
                continue
            if not buffer:
                start = number
            buffer.append(line[position:match.end()])
            flush()
            position = match.end()
        rest = line[position:]
        if rest.strip():
            if not buffer:
                start = number
            buffer.append(rest)
        if BLOCK_LINE.match(line):
            flush()
    flush()
    return found


def check(rules: Rules, draft: str) -> list[tuple[int, int, int, str, str]]:
    """Return (line, col, rule order, rule id, message) tuples."""
    lines = draft.splitlines()
    hits: list[tuple[int, int, int, str, str]] = []

    for phrase in rules.bans:
        pattern = phrase_pattern(phrase)
        for number, line in enumerate(lines, start=1):
            for match in pattern.finditer(line):
                hits.append((number, match.start() + 1, 0, "banned", f'"{match.group(0)}" is banned'))

    for preferred, avoided in rules.prefers:
        pattern = phrase_pattern(avoided)
        for number, line in enumerate(lines, start=1):
            for match in pattern.finditer(line):
                hits.append((number, match.start() + 1, 1, "prefer", f'use "{preferred}" not "{match.group(0)}"'))

    if rules.max_sentence_words is not None:
        for number, text in sentences(lines):
            count = len(WORD.findall(text))
            if count > rules.max_sentence_words:
                opening = " ".join(text.split()[:6])
                hits.append((number, 1, 2, "sentence-length",
                             f'{count} words (max {rules.max_sentence_words}): "{opening} ..."'))

    if rules.exclamations != "allow":
        allowed = 0 if rules.exclamations == "none" else int(rules.exclamations)
        policy = "none" if rules.exclamations == "none" else f"max {allowed}"
        seen = 0
        for number, line in enumerate(lines, start=1):
            for col, char in enumerate(line, start=1):
                if char == "!":
                    seen += 1
                    if seen > allowed:
                        hits.append((number, col, 3, "exclamation", f"exclamation mark #{seen} (policy: {policy})"))

    return sorted(hits)


def run_check(clients_dir: Path, slug: str, draft_path: Path) -> int:
    profile = load_client(clients_dir, slug)
    guide_path = clients_dir / slug / "brand-voice.md"
    rules = parse_rules(read_text(guide_path, "brand-voice guidelines"), guide_path)
    if rules.client != slug:
        raise InputError(f"{guide_path}: voice-rules block belongs to client {rules.client!r}, not {slug!r}; "
                         "refusing to apply another client's rules")
    draft = read_text(draft_path, "draft")
    hits = check(rules, draft)

    print(f"client: {slug} ({profile['name']})")
    print(f"rules: {guide_path.as_posix()} (updated {rules.updated}, status {rules.status})")
    print(f"draft: {draft_path.as_posix()}")
    for line, col, _, rule_id, message in hits:
        print(f"  L{line}:{col} {rule_id}: {message}")
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit[3]] = counts.get(hit[3], 0) + 1
    breakdown = ", ".join(f"{k} {counts[k]}" for k in ("banned", "prefer", "sentence-length", "exclamation") if k in counts)
    print(f"result: {len(hits)} violation(s)" + (f" ({breakdown})" if breakdown else ""))
    return 1 if hits else 0


def run_list(clients_dir: Path) -> int:
    if not clients_dir.is_dir():
        raise InputError(f"clients directory not found: {clients_dir}")
    problems = 0
    slugs = sorted(p.name for p in clients_dir.iterdir() if p.is_dir())
    for slug in slugs:
        try:
            profile = load_client(clients_dir, slug)
            guide_path = clients_dir / slug / "brand-voice.md"
            if not guide_path.is_file():
                print(f"{slug} ({profile['name']}): no brand-voice.md")
                continue
            rules = parse_rules(read_text(guide_path, "brand-voice guidelines"), guide_path)
            if rules.client != slug:
                raise InputError(f"{guide_path}: voice-rules block names client {rules.client!r}")
            print(f"{slug} ({profile['name']}): updated {rules.updated}, status {rules.status}, "
                  f"{len(rules.bans)} banned, {len(rules.prefers)} prefer")
        except InputError as exc:
            problems += 1
            print(f"{slug}: INVALID - {exc}")
    print(f"clients: {len(slugs)}, invalid: {problems}")
    return 2 if problems else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clients-dir", default="clients", type=Path)
    parser.add_argument("--client", help="client slug, e.g. harbor-and-vine")
    parser.add_argument("--draft", type=Path, help="UTF-8 text file to check")
    parser.add_argument("--list", action="store_true", help="list clients and their guideline status")
    args = parser.parse_args(argv)

    try:
        if args.list:
            if args.client or args.draft:
                raise InputError("--list takes no --client or --draft")
            return run_list(args.clients_dir)
        if not args.client or not args.draft:
            raise InputError("--client and --draft are both required (or use --list)")
        return run_check(args.clients_dir, args.client, args.draft)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
