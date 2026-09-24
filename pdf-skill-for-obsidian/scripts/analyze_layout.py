#!/usr/bin/env python3
"""Find low/orphaned headings, split callouts, and broken heading groups."""

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
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)|!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
RASTER_SUFFIXES = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
LOW_HEADING_LEVELS = {2, 3, 4, 5}
LOW_HEADING_FRACTION = 0.85


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


@dataclass(frozen=True)
class Callout:
    start: int
    end: int
    label: str
    lines: list[str]


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
    for size in range(maximum, 0, -1):
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


def locate_all(pages: list[PageData], candidates: list[list[str]]) -> list[Location]:
    """Return every occurrence for the longest candidate that has a match."""
    for candidate in candidates:
        matches: list[Location] = []
        for page_index, page in enumerate(pages):
            for start in sequence_starts(page.tokens, candidate):
                matches.append(Location(page_index + 1, page.tops[start], page.height))
        if matches:
            return matches
    return []


def nearest_location_before(
    candidates: list[Location], boundary: Location
) -> Location | None:
    earlier = [candidate for candidate in candidates if position(candidate) < position(boundary)]
    return max(earlier, key=position) if earlier else None


def collect_callouts(lines: list[str]) -> list[Callout]:
    callouts: list[Callout] = []
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
        callouts.append(Callout(start, end, label, lines[start:end]))
    return callouts


def legacy_spacer_end(lines: list[str], index: int) -> int | None:
    if index + 4 >= len(lines):
        return None
    if lines[index].strip() != "$$" or lines[index + 1].strip() != r"\begin{align}":
        return None
    if not re.fullmatch(r"\\+", lines[index + 2].strip()):
        return None
    if lines[index + 3].strip() != r"\end{align}" or lines[index + 4].strip() != "$$":
        return None
    return index + 5


def skip_layout_only(lines: list[str], index: int) -> int:
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        spacer_end = legacy_spacer_end(lines, index)
        if spacer_end is not None:
            index = spacer_end
            continue
        if MANAGED_START in lines[index]:
            index += 1
            while index < len(lines) and MANAGED_END not in lines[index]:
                index += 1
            index += index < len(lines)
            continue
        break
    return index


def is_plain_lead_in(line: str) -> bool:
    stripped = line.strip()
    if not stripped or HEADING_RE.match(line) or CALLOUT_RE.match(line):
        return False
    if stripped.startswith((">", "```", "~~~", "$$", "|", "![", "![[")):
        return False
    return re.match(r"^\s{0,3}(?:[-+*]|\d+[.)])\s+", line) is None


def heading_associated_callout(
    lines: list[str], heading_index: int, callouts_by_start: dict[int, Callout]
) -> Callout | None:
    """Return the first callout governed by a heading.

    A governed callout is either the heading's first block or follows one short
    introductory paragraph. Lists, media, tables, code, math, another heading,
    or more than one paragraph end the association.
    """
    index = skip_layout_only(lines, heading_index + 1)
    if index in callouts_by_start:
        return callouts_by_start[index]
    if index >= len(lines) or not is_plain_lead_in(lines[index]):
        return None
    while index < len(lines) and lines[index].strip():
        if not is_plain_lead_in(lines[index]):
            return None
        index += 1
    index = skip_layout_only(lines, index)
    return callouts_by_start.get(index)


def markdown_image_target(line: str) -> str | None:
    match = MARKDOWN_IMAGE_RE.search(line)
    if not match:
        return None
    return (match.group(1) or match.group(2)).split("#", 1)[0].split("?", 1)[0]


def heading_is_too_low(level: int, fraction: float) -> bool:
    return level in LOW_HEADING_LEVELS and fraction >= LOW_HEADING_FRACTION


def next_anchor_tokens(lines: list[str], start: int) -> list[str] | None:
    index = skip_layout_only(lines, start)
    while index < len(lines):
        found = line_tokens(lines[index])
        if found:
            return found
        index += 1
    return None


def position(location: Location) -> tuple[int, float]:
    return location.page, location.top


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
    pdf_image_locations: list[Location] = []
    with pdfplumber.open(pdf) as document:
        pages: list[PageData] = []
        for page_index, page in enumerate(document.pages):
            page_tokens: list[str] = []
            page_tops: list[float] = []
            for word in page.extract_words(use_text_flow=True):
                word_tokens = tokens(word["text"])
                page_tokens.extend(word_tokens)
                page_tops.extend([float(word["top"])] * len(word_tokens))
            pages.append(PageData(page_tokens, page_tops, float(page.height)))
            for image in sorted(page.images, key=lambda item: float(item.get("top", 0.0))):
                pdf_image_locations.append(
                    Location(page_index + 1, float(image.get("top", 0.0)), float(page.height))
                )

    violations: list[Violation] = []
    unresolved: list[Unresolved] = []
    callouts = collect_callouts(lines)
    callouts_by_start = {callout.start: callout for callout in callouts}
    callout_end_locations: dict[int, Location] = {}

    for callout in callouts:
        token_lines = [line_tokens(line) for line in callout.lines]
        token_lines = [line for line in token_lines if line]
        if not token_lines:
            unresolved.append(
                Unresolved("callout", callout.start + 1, callout.end, callout.label, "visible text")
            )
            continue
        start_tokens = token_lines[0]
        if len(start_tokens) < 3 and len(token_lines) > 1:
            start_tokens = start_tokens + token_lines[1]
        end_tokens = token_lines[-1]
        start_locations = locate(pages, anchor_windows(start_tokens, False))
        end_locations = locate(pages, anchor_windows(end_tokens, True))
        media_targets = [
            target
            for target in (
                markdown_image_target(lines[index])
                for index in range(callout.start, callout.end)
            )
            if target is not None
        ]
        raster_targets = [
            target for target in media_targets if Path(target).suffix.casefold() in RASTER_SUFFIXES
        ]
        media_locations: list[Location] = []
        missing: list[str] = []
        if not start_locations:
            missing.append("start")
        if media_targets and len(raster_targets) != len(media_targets):
            missing.append("non-raster media")
        if raster_targets and start_locations:
            boundary_tokens = next_anchor_tokens(lines, callout.end)
            boundary_locations = (
                locate(pages, anchor_windows(boundary_tokens, False))
                if boundary_tokens
                else []
            )
            if boundary_locations:
                media_locations = [
                    location
                    for location in pdf_image_locations
                    if position(start_locations[0]) < position(location) < position(boundary_locations[0])
                ]
            if len(media_locations) != len(raster_targets):
                media_locations = []
                missing.append("raster image mapping")
        if not end_locations and not media_locations:
            missing.append("end")
        if missing:
            unresolved.append(
                Unresolved(
                    "callout", callout.start + 1, callout.end, callout.label, ", ".join(missing)
                )
            )
            continue
        end_location = max(end_locations + media_locations, key=lambda item: (item.page, item.top))
        callout_end_locations[callout.start] = end_location
        if start_locations[0].page != end_location.page:
            violations.append(
                Violation(
                    "callout",
                    callout.start + 1,
                    callout.end,
                    callout.label,
                    start_locations[0].page,
                    end_location.page,
                )
            )

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
        content_locations = locate(pages, anchor_windows(following_tokens, False))
        heading_candidates = anchor_windows(heading_tokens, False)
        heading_locations = locate(pages, heading_candidates)
        if not heading_locations and content_locations:
            inferred_heading = nearest_location_before(
                locate_all(pages, heading_candidates), content_locations[0]
            )
            if inferred_heading:
                heading_locations = [inferred_heading]
        missing: list[str] = []
        if not heading_locations:
            missing.append("heading")
        if not content_locations:
            missing.append("following content")
        governed_callout = heading_associated_callout(lines, index, callouts_by_start)
        governed_end = (
            callout_end_locations.get(governed_callout.start)
            if governed_callout is not None
            else None
        )
        if missing:
            unresolved.append(
                Unresolved("heading", index + 1, end_index + 1, label, ", ".join(missing))
            )
        elif governed_end is None and heading_locations[0].page != content_locations[0].page:
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

        if heading_locations:
            location = heading_locations[0]
            fraction = location.top / location.pageHeight
            if heading_is_too_low(level, fraction):
                violations.append(
                    Violation(
                        "heading-bottom-fifteen-percent",
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

        if (
            governed_callout is not None
            and heading_locations
            and governed_end is not None
            and heading_locations[0].page != governed_end.page
        ):
            violations.append(
                Violation(
                    "heading-callout-group",
                    index + 1,
                    governed_callout.end,
                    label,
                    heading_locations[0].page,
                    governed_end.page,
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
    if result["violations"]:
        return 1
    return 2 if result["unresolved"] else 0


if __name__ == "__main__":
    sys.exit(main())
