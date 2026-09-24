#!/usr/bin/env python3
"""Find orphaned headings and split Obsidian callouts in an exported PDF."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pdfplumber


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CALLOUT_RE = re.compile(r"^\s*>\s*\[!([^\]]+)\][+-]?\s*(.*?)\s*$")
MANAGED_START = "obsidian-pdf-formatter:start"
MANAGED_END = "obsidian-pdf-formatter:end"
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass
class Violation:
    kind: str
    line: int
    endLine: int
    label: str
    startPage: int
    endPage: int
    top: float | None = None
    pageHeight: float | None = None
    pageFraction: float | None = None


@dataclass
class Unresolved:
    kind: str
    line: int
    endLine: int
    label: str
    missing: str


@dataclass
class PageData:
    tokens: list[str]
    tops: list[float]
    height: float


@dataclass
class Location:
    page: int
    top: float
    pageHeight: float


def tokens(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(text)]


def visible_text(line: str) -> str:
    line = re.sub(r"^\s*(?:>\s*)+", "", line)
    line = re.sub(r"^\s{0,3}(?:[-+*]|\d+[.)])\s+", "", line)
    line = re.sub(r"^#{1,6}\s+", "", line)
    line = re.sub(r"^\[![^\]]+\][+-]?\s*", "", line)
    line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", line)
    line = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line)
    line = re.sub(r"<[^>]+>", " ", line)
    line = re.sub(r"`+", "", line)
    line = re.sub(r"[*_=~]", "", line)
    line = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\{([^{}]*)\}", r"\1", line)
    line = re.sub(r"\\[A-Za-z]+", " ", line)
    line = re.sub(r"[$^{}]", " ", line)
    return html.unescape(line)


def line_tokens(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("```") or stripped.startswith("~~~"):
        return []
    if stripped in {"$$", r"\begin{align}", r"\end{align}"}:
        return []
    if stripped.startswith("<!--") or stripped.endswith("-->"):
        return []
    return tokens(visible_text(line))


def anchor_windows(block_tokens: list[str], from_end: bool) -> list[list[str]]:
    if not block_tokens:
        return []
    maximum = min(10, len(block_tokens))
    windows: list[list[str]] = []
    for size in range(maximum, 2, -1):
        window = block_tokens[-size:] if from_end else block_tokens[:size]
        if window not in windows:
            windows.append(window)
    return windows


def sequence_starts(haystack: list[str], needle: list[str]) -> list[int]:
    if len(needle) > len(haystack):
        return []
    return [
        index
        for index, value in enumerate(haystack)
        if value == needle[0] and haystack[index : index + len(needle)] == needle
    ]


def locate(pages: list[PageData], candidates: list[list[str]]) -> list[Location]:
    for candidate in candidates:
        matches: list[Location] = []
        for page_index, page in enumerate(pages):
            starts = sequence_starts(page.tokens, candidate)
            if starts:
                matches.append(
                    Location(page_index + 1, page.tops[starts[0]], page.height)
                )
        if len(matches) == 1:
            return matches
    return []


def collect_callouts(lines: list[str]) -> list[tuple[int, int, str, list[str]]]:
    callouts: list[tuple[int, int, str, list[str]]] = []
    index = 0
    while index < len(lines):
        match = CALLOUT_RE.match(lines[index])
        if not match:
            index += 1
            continue
        start = index
        callout_type, title = match.groups()
        index += 1
        while index < len(lines) and re.match(r"^\s*>", lines[index]):
            index += 1
        end = index
        label = title or callout_type
        callouts.append((start, end, label, lines[start:end]))
    return callouts


def first_substantive_tokens(lines: list[str], start: int) -> tuple[int, list[str]] | None:
    index = start
    managed = False
    while index < len(lines):
        line = lines[index]
        if MANAGED_START in line:
            managed = True
            index += 1
            continue
        if managed:
            if MANAGED_END in line:
                managed = False
            index += 1
            continue
        heading = HEADING_RE.match(line)
        if heading:
            index += 1
            continue
        found = line_tokens(line)
        if found:
            return index, found
        index += 1
    return None


def analyze(note: Path, pdf: Path) -> dict[str, object]:
    lines = note.read_text(encoding="utf-8-sig").splitlines()
    with pdfplumber.open(pdf) as document:
        pages: list[PageData] = []
        for page in document.pages:
            page_tokens: list[str] = []
            page_tops: list[float] = []
            for word in page.extract_words(use_text_flow=True):
                word_tokens = tokens(word["text"])
                page_tokens.extend(word_tokens)
                page_tops.extend([float(word["top"])] * len(word_tokens))
            pages.append(PageData(page_tokens, page_tops, float(page.height)))

    violations: list[Violation] = []
    unresolved: list[Unresolved] = []

    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        label = visible_text(match.group(2)).strip()
        heading_tokens = tokens(label)
        following = first_substantive_tokens(lines, index + 1)
        if not heading_tokens or not following:
            continue
        end_index, following_tokens = following
        heading_locations = locate(pages, anchor_windows(heading_tokens, False))
        content_locations = locate(pages, anchor_windows(following_tokens, False))
        missing: list[str] = []
        if not heading_locations:
            missing.append("heading")
        if not content_locations:
            missing.append("following content")
        if missing:
            unresolved.append(
                Unresolved("heading", index + 1, end_index + 1, label, ", ".join(missing))
            )
        elif heading_locations[0].page != content_locations[0].page:
            violations.append(
                Violation(
                    "heading",
                    index + 1,
                    end_index + 1,
                    label,
                    heading_locations[0].page,
                    content_locations[0].page,
                )
            )

        if level in (2, 3) and heading_locations:
            location = heading_locations[0]
            fraction = location.top / location.pageHeight
            if fraction >= 0.9:
                violations.append(
                    Violation(
                        "heading-bottom-tenth",
                        index + 1,
                        index + 1,
                        label,
                        location.page,
                        location.page,
                        round(location.top, 2),
                        round(location.pageHeight, 2),
                        round(fraction, 4),
                    )
                )

    for start, end, label, block in collect_callouts(lines):
        token_lines = [line_tokens(line) for line in block]
        token_lines = [line for line in token_lines if line]
        if not token_lines:
            unresolved.append(Unresolved("callout", start + 1, end, label, "visible text"))
            continue
        start_tokens = token_lines[0]
        if len(start_tokens) < 3 and len(token_lines) > 1:
            start_tokens = start_tokens + token_lines[1]
        end_tokens = token_lines[-1]
        start_locations = locate(pages, anchor_windows(start_tokens, False))
        end_locations = locate(pages, anchor_windows(end_tokens, True))
        missing: list[str] = []
        if not start_locations:
            missing.append("start")
        if not end_locations:
            missing.append("end")
        if missing:
            unresolved.append(
                Unresolved("callout", start + 1, end, label, ", ".join(missing))
            )
        elif start_locations[0].page != end_locations[0].page:
            violations.append(
                Violation(
                    "callout",
                    start + 1,
                    end,
                    label,
                    start_locations[0].page,
                    end_locations[0].page,
                )
            )

    violations.sort(key=lambda item: (item.line, item.kind))
    unresolved.sort(key=lambda item: (item.line, item.kind))
    return {
        "note": str(note),
        "pdf": str(pdf),
        "pages": len(pages),
        "violations": [asdict(item) for item in violations],
        "unresolved": [asdict(item) for item in unresolved],
    }


def path_arg(value: str, suffix: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file() or path.suffix.casefold() != suffix:
        raise argparse.ArgumentTypeError(f"Expected an existing {suffix} file: {value}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("note", type=lambda value: path_arg(value, ".md"))
    parser.add_argument("pdf", type=lambda value: path_arg(value, ".pdf"))
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = analyze(args.note, args.pdf)
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 1 if result["violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
