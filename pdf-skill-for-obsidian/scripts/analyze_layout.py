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
from statistics import median

import pdfplumber


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CALLOUT_RE = re.compile(r"^\s*>\s*\[!([^\]]+)\][+-]?\s*(.*?)\s*$")
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)|!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
RASTER_SUFFIXES = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
LOW_HEADING_LEVELS = {2, 3, 4, 5}
LOW_HEADING_REVIEW_FRACTION = 0.85
LOW_HEADING_HARD_FRACTION = 0.90
MIN_REVIEW_BAND_LINES = 2
FALLBACK_LINE_HEIGHT = 24.0
MAX_LINE_HEIGHT = 48.0
LIST_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)(?:[-+*]|\d+[.)])\s+")
LIST_ANY_RE = re.compile(r"^\s{0,3}(?:[-+*]|\d+[.)])\s+")
ORDERED_ITEM_RE = re.compile(r"^\s{0,3}\d+[.)]\s+")
NESTED_LIST_RE = re.compile(r"^\s+(?:[-+*]|\d+[.)])\s+")
BLOCKQUOTE_RE = re.compile(r"^\s*>")
BACKSLASH_RE = re.compile(r"\\+")
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")

QUOTE_PREFIX_RE = re.compile(r"^\s*(?:>\s*)+")
LIST_PREFIX_RE = re.compile(r"^\s{0,3}(?:[-+*]|\d+[.)])\s+")
HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s+")
CALLOUT_PREFIX_RE = re.compile(r"^\[![^\]]+\][+-]?\s*")
INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
WIKI_IMAGE_RE = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
WIKI_PIPE_RE = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")
WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
HTML_TAG_RE = re.compile(r"<[^>]+>")
BACKTICKS_RE = re.compile(r"`+")
EMPHASIS_RE = re.compile(r"[*_=~]")
MATH_TEXT_RE = re.compile(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\{([^{}]*)\}")
LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
MATH_CHARS_RE = re.compile(r"[$^{}]")


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
    firstTop: float | None = None
    positions: dict[str, list[int]] | None = None


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


def strip_quote(line: str) -> str:
    return QUOTE_PREFIX_RE.sub("", line).strip()


def fenced_code_lines(lines: list[str]) -> list[bool]:
    """Mark lines inside fenced code so literal examples are not layout content."""
    mask = [False] * len(lines)
    fence_char: str | None = None
    fence_length = 0
    for index, line in enumerate(lines):
        content = QUOTE_PREFIX_RE.sub("", line)
        match = FENCE_RE.match(content)
        if fence_char is None:
            if match:
                fence_char = match.group(1)[0]
                fence_length = len(match.group(1))
                mask[index] = True
        else:
            mask[index] = True
            if (
                match
                and match.group(1)[0] == fence_char
                and len(match.group(1)) >= fence_length
                and not match.group(2).strip()
            ):
                fence_char = None
                fence_length = 0
    return mask


def is_comment_line(content: str) -> bool:
    return (
        content.startswith("<!--")
        or content.endswith("-->")
        or content.startswith("%%")
        or content.endswith("%%")
    )


def visible_text(line: str) -> str:
    line = QUOTE_PREFIX_RE.sub("", line)
    line = LIST_PREFIX_RE.sub("", line)
    line = HEADING_PREFIX_RE.sub("", line)
    line = CALLOUT_PREFIX_RE.sub("", line)
    line = INLINE_IMAGE_RE.sub(r"\1", line)
    line = WIKI_IMAGE_RE.sub(r"\1", line)
    line = LINK_RE.sub(r"\1", line)
    line = WIKI_PIPE_RE.sub(r"\2", line)
    line = WIKI_LINK_RE.sub(r"\1", line)
    line = HTML_TAG_RE.sub(" ", line)
    line = BACKTICKS_RE.sub("", line)
    line = EMPHASIS_RE.sub("", line)
    line = MATH_TEXT_RE.sub(r"\1", line)
    line = LATEX_COMMAND_RE.sub(" ", line)
    line = MATH_CHARS_RE.sub(" ", line)
    return html.unescape(line)


def line_tokens(line: str) -> list[str]:
    content = strip_quote(line)
    if not content or content.startswith("```") or content.startswith("~~~"):
        return []
    if content in {"$$", r"\begin{align}", r"\end{align}"}:
        return []
    if is_comment_line(content):
        return []
    return tokens(visible_text(line))


def anchor_windows(block_tokens: list[str], from_end: bool) -> list[list[str]]:
    if not block_tokens:
        return []
    maximum = min(10, len(block_tokens))
    windows: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for size in range(maximum, 0, -1):
        window = block_tokens[-size:] if from_end else block_tokens[:size]
        key = tuple(window)
        if key not in seen:
            seen.add(key)
            windows.append(window)
    return windows


def page_positions(page: PageData) -> dict[str, list[int]]:
    """Cache each token's offsets so anchors are verified only at real hits."""
    if page.positions is None:
        page.positions = {}
        for offset, token in enumerate(page.tokens):
            page.positions.setdefault(token, []).append(offset)
    return page.positions


def match_candidate(pages: list[PageData], candidate: list[str]) -> list[Location]:
    """Return every occurrence, checking only positions of the first token."""
    matches: list[Location] = []
    size = len(candidate)
    for page_index, page in enumerate(pages):
        for offset in page_positions(page).get(candidate[0], ()):
            if page.tokens[offset : offset + size] == candidate:
                matches.append(Location(page_index + 1, page.tops[offset], page.height))
    return matches


def locate(pages: list[PageData], candidates: list[list[str]]) -> list[Location]:
    for candidate in candidates:
        matches = match_candidate(pages, candidate)
        if len(matches) == 1:
            return matches
    return []


def locate_with_all(
    pages: list[PageData], candidates: list[list[str]]
) -> tuple[list[Location], list[Location]]:
    """Return the unique match, plus the first non-empty match set.

    The second value matches ``locate_all`` for the same candidates, so callers
    that need both results can walk the candidates once instead of twice.
    """
    first_nonempty: list[Location] = []
    for candidate in candidates:
        matches = match_candidate(pages, candidate)
        if matches and not first_nonempty:
            first_nonempty = matches
        if len(matches) == 1:
            return matches, first_nonempty
    return [], first_nonempty


def locate_all(pages: list[PageData], candidates: list[list[str]]) -> list[Location]:
    """Return every occurrence for the longest candidate that has a match."""
    for candidate in candidates:
        matches = match_candidate(pages, candidate)
        if matches:
            return matches
    return []


def nearest_location_before(
    candidates: list[Location], boundary: Location
) -> Location | None:
    earlier = [candidate for candidate in candidates if position(candidate) < position(boundary)]
    return max(earlier, key=position) if earlier else None


def resolve_short_anchor(
    pages: list[PageData], anchor_tokens: list[str], boundary: Location | None
) -> list[Location]:
    """Locate a short/repeated anchor using the nearest following boundary.

    Callout titles are often only one or two words.  Concatenating their body
    text makes an impossible anchor when the header and body straddle a page.
    Keep the header anchor intact and disambiguate repeated occurrences by
    choosing the closest one before the block's end or next source anchor.
    """
    candidates = anchor_windows(anchor_tokens, False)
    unique, all_matches = locate_with_all(pages, candidates)
    if unique:
        return unique
    if boundary is None:
        return []
    nearest = nearest_location_before(all_matches, boundary)
    return [nearest] if nearest else []


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
        while index < len(lines) and BLOCKQUOTE_RE.match(lines[index]):
            index += 1
        end = index
        label = title or callout_type
        callouts.append(Callout(start, end, label, lines[start:end]))
    return callouts


def legacy_spacer_end(
    lines: list[str], index: int, literal: list[bool] | None = None
) -> int | None:
    if literal is None:
        literal = fenced_code_lines(lines)
    if index + 4 >= len(lines) or any(literal[index : index + 5]):
        return None
    if strip_quote(lines[index]) != "$$" or strip_quote(lines[index + 1]) != r"\begin{align}":
        return None
    if not BACKSLASH_RE.fullmatch(strip_quote(lines[index + 2])):
        return None
    if strip_quote(lines[index + 3]) != r"\end{align}" or strip_quote(lines[index + 4]) != "$$":
        return None
    return index + 5


def skip_layout_only(
    lines: list[str], index: int, literal: list[bool] | None = None
) -> int:
    if literal is None:
        literal = fenced_code_lines(lines)
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        if is_comment_line(strip_quote(lines[index])):
            index += 1
            continue
        spacer_end = legacy_spacer_end(lines, index, literal)
        if spacer_end is not None:
            index = spacer_end
            continue
        break
    return index


def is_plain_lead_in(line: str) -> bool:
    stripped = line.strip()
    if not stripped or HEADING_RE.match(line) or CALLOUT_RE.match(line):
        return False
    if stripped.startswith((">", "```", "~~~", "$$", "|", "![", "![[")):
        return False
    return LIST_ANY_RE.match(line) is None


def heading_associated_callout(
    lines: list[str],
    heading_index: int,
    callouts_by_start: dict[int, Callout],
    literal: list[bool] | None = None,
) -> Callout | None:
    """Return the first callout governed by a heading.

    A governed callout is either the heading's first block or follows one short
    introductory paragraph. Lists, media, tables, code, math, another heading,
    or more than one paragraph end the association.
    """
    index = skip_layout_only(lines, heading_index + 1, literal)
    if index in callouts_by_start:
        return callouts_by_start[index]
    if index >= len(lines) or not is_plain_lead_in(lines[index]):
        return None
    while index < len(lines) and lines[index].strip():
        if not is_plain_lead_in(lines[index]):
            return None
        index += 1
    index = skip_layout_only(lines, index, literal)
    return callouts_by_start.get(index)


def markdown_image_target(line: str) -> str | None:
    match = MARKDOWN_IMAGE_RE.search(line)
    if not match:
        return None
    return (match.group(1) or match.group(2)).split("#", 1)[0].split("?", 1)[0]


def media_led_block(
    lines: list[str], start: int, literal: list[bool] | None = None
) -> tuple[int, int] | None:
    """Return a media-first block and any immediately accompanying list.

    The returned pair is ``(media line, end-exclusive line)``. Tightly spaced
    items of either list kind are included. Blank lines may continue a numbered
    list, but a blank line ends a bulleted list's association. A heading,
    callout, paragraph, table, code block, or second media item ends the block.
    """
    media_index = skip_layout_only(lines, start, literal)
    if media_index >= len(lines) or markdown_image_target(lines[media_index]) is None:
        return None
    index = media_index + 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or not LIST_ANY_RE.match(lines[index]):
        return media_index, media_index + 1
    ordered = ORDERED_ITEM_RE.match(lines[index]) is not None
    end = index + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if not stripped:
            probe = end + 1
            while probe < len(lines) and not lines[probe].strip():
                probe += 1
            if probe < len(lines) and ordered and ORDERED_ITEM_RE.match(lines[probe]):
                end = probe + 1
                continue
            break
        if HEADING_RE.match(lines[end]) or CALLOUT_RE.match(lines[end]):
            break
        if markdown_image_target(lines[end]) is not None or stripped.startswith(("|", "```", "~~~", "$$")):
            break
        if NESTED_LIST_RE.match(lines[end]):
            end += 1
            continue
        item = LIST_ANY_RE.match(lines[end])
        item_is_ordered = ORDERED_ITEM_RE.match(lines[end]) is not None
        if item is not None and item_is_ordered == ordered:
            end += 1
            continue
        break
    return media_index, end


def first_nested_list_group(
    lines: list[str], heading_index: int, literal: list[bool] | None = None
) -> tuple[int, int] | None:
    """Find a heading's first list item when that item has nested list content.

    Permit one short lead-in paragraph. Return the first item's source span,
    ending before its next sibling, so later list items may flow normally.
    """
    index = skip_layout_only(lines, heading_index + 1, literal)
    if index < len(lines) and is_plain_lead_in(lines[index]):
        lead_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            if LIST_ITEM_RE.match(lines[index]):
                break
            if not is_plain_lead_in(lines[index]):
                return None
            lead_lines.append(lines[index])
            index += 1
        if len(lead_lines) > 2 or sum(len(line_tokens(line)) for line in lead_lines) > 30:
            return None
        index = skip_layout_only(lines, index, literal)
    if index >= len(lines):
        return None
    first = LIST_ITEM_RE.match(lines[index])
    if first is None:
        return None
    base_indent = len(first.group("indent").expandtabs(4))
    nested = False
    end = index + 1
    cursor = end
    while cursor < len(lines):
        line = lines[cursor]
        if not line.strip():
            cursor += 1
            continue
        if HEADING_RE.match(line) or CALLOUT_RE.match(line):
            break
        item = LIST_ITEM_RE.match(line)
        indent = len((item.group("indent") if item else line[: len(line) - len(line.lstrip())]).expandtabs(4))
        if indent <= base_indent:
            break
        if item is not None:
            nested = True
        end = cursor + 1
        cursor += 1
    return (index, end) if nested else None


def heading_is_too_low(
    level: int, fraction: float, same_page_following_lines: int = 0
) -> bool:
    """Return whether a heading is unacceptably low on its page.

    The bottom 10% is always too low for H2-H5. The preceding 5% is a
    review band: keep the heading when at least two rendered lines of its
    following content remain on the page. This avoids moving a nearly fitting
    group and creating disproportionate whitespace on the old page.
    """
    if level not in LOW_HEADING_LEVELS:
        return False
    if fraction >= LOW_HEADING_HARD_FRACTION:
        return True
    return (
        fraction >= LOW_HEADING_REVIEW_FRACTION
        and same_page_following_lines < MIN_REVIEW_BAND_LINES
    )


def rendered_lines_after(location: Location, pages: list[PageData]) -> int:
    """Count distinct rendered text lines below a location on the same page."""
    page = pages[location.page - 1]
    return len({round(top, 1) for top in page.tops if top > location.top + 2.0})


def next_anchor_tokens(
    lines: list[str], start: int, literal: list[bool] | None = None
) -> list[str] | None:
    index = skip_layout_only(lines, start, literal)
    while index < len(lines):
        found = line_tokens(lines[index])
        if found:
            return found
        index += 1
    return None


def position(location: Location) -> tuple[int, float]:
    return location.page, location.top


def estimated_line_height(page: PageData) -> float:
    """Estimate one rendered text line from distinct tops on the page.

    Falls back to a conservative constant when the page has fewer than two
    distinct text positions (for example, a heading alone on the page). The
    estimate is capped so a page with only a few widely separated lines cannot
    inflate the one-line threshold used by the overcompensation check.
    """
    tops = sorted({round(top, 1) for top in page.tops})
    deltas = [
        later - earlier
        for earlier, later in zip(tops, tops[1:])
        if later - earlier > 1.0
    ]
    if not deltas:
        return FALLBACK_LINE_HEIGHT
    return min(median(deltas), MAX_LINE_HEIGHT)


def page_top_overcompensated(
    location: Location,
    pages: list[PageData],
    normal_top: float | None = None,
    line_height: float | None = None,
) -> bool:
    """Detect a moved target stranded materially below the normal top margin."""
    page = pages[location.page - 1]
    if page.firstTop is None:
        return False
    if normal_top is None:
        observed = [item.firstTop for item in pages if item.firstTop is not None]
        if not observed:
            return False
        normal_top = min(observed)
    if line_height is None:
        line_height = estimated_line_height(page)
    one_rendered_line = line_height * 1.5
    is_first_visible_item = abs(location.top - page.firstTop) <= 2.0
    return is_first_visible_item and location.top > normal_top + one_rendered_line


def first_substantive_tokens(lines: list[str], start: int) -> tuple[int, list[str]] | None:
    index = start
    while index < len(lines):
        line = lines[index]
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
            visible_word_tops: list[float] = []
            for word in page.extract_words(use_text_flow=True):
                visible_word_tops.append(float(word["top"]))
                word_tokens = tokens(word["text"])
                page_tokens.extend(word_tokens)
                page_tops.extend([float(word["top"])] * len(word_tokens))
            # Raster media is visible content even when it contains no PDF text.
            visible_word_tops.extend(
                float(image["top"]) for image in page.images
                if image.get("top") is not None
            )
            pages.append(
                PageData(
                    page_tokens,
                    page_tops,
                    float(page.height),
                    min(visible_word_tops) if visible_word_tops else None,
                )
            )
            for image in sorted(page.images, key=lambda item: float(item.get("top", 0.0))):
                top = image.get("top")
                if top is None:
                    continue
                pdf_image_locations.append(
                    Location(page_index + 1, float(top), float(page.height))
                )

    violations: list[Violation] = []
    unresolved: list[Unresolved] = []
    observed_tops = [item.firstTop for item in pages if item.firstTop is not None]
    normal_top = min(observed_tops) if observed_tops else None
    line_heights = [estimated_line_height(page) for page in pages]
    literal = fenced_code_lines(lines)
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
        end_tokens = token_lines[-1]
        end_locations = locate(pages, anchor_windows(end_tokens, True))
        boundary_tokens = next_anchor_tokens(lines, callout.end, literal)
        boundary_locations = (
            locate(pages, anchor_windows(boundary_tokens, False)) if boundary_tokens else []
        )
        disambiguation_boundary = (
            end_locations[0] if end_locations else (boundary_locations[0] if boundary_locations else None)
        )
        start_locations = resolve_short_anchor(pages, start_tokens, disambiguation_boundary)
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
        if page_top_overcompensated(
            start_locations[0],
            pages,
            normal_top,
            line_heights[start_locations[0].page - 1],
        ):
            violations.append(
                Violation(
                    "page-top-overcompensation",
                    callout.start + 1,
                    callout.end,
                    callout.label,
                    start_locations[0].page,
                    start_locations[0].page,
                    round(start_locations[0].top, 2),
                    round(start_locations[0].pageHeight, 2),
                    round(start_locations[0].top / start_locations[0].pageHeight, 4),
                )
            )

    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        label = visible_text(match.group(2)).strip()
        heading_tokens = tokens(label)
        media_block = media_led_block(lines, index + 1, literal)
        following = first_substantive_tokens(lines, index + 1)
        if not heading_tokens or not following:
            continue
        end_index, following_tokens = following
        content_locations = locate(pages, anchor_windows(following_tokens, False))
        heading_candidates = anchor_windows(heading_tokens, False)
        heading_locations, heading_matches = locate_with_all(pages, heading_candidates)
        if not heading_locations and content_locations:
            inferred_heading = nearest_location_before(
                heading_matches, content_locations[0]
            )
            if inferred_heading:
                heading_locations = [inferred_heading]
        missing: list[str] = []
        if not heading_locations:
            missing.append("heading")
        if not content_locations and media_block is None:
            missing.append("following content")
        governed_callout = heading_associated_callout(
            lines, index, callouts_by_start, literal
        )
        nested_list_group = first_nested_list_group(lines, index, literal)
        governed_end = (
            callout_end_locations.get(governed_callout.start)
            if governed_callout is not None
            else None
        )
        if missing:
            unresolved.append(
                Unresolved("heading", index + 1, end_index + 1, label, ", ".join(missing))
            )
        elif (
            governed_end is None
            and content_locations
            and heading_locations[0].page != content_locations[0].page
        ):
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
            following_lines = rendered_lines_after(location, pages)
            if heading_is_too_low(level, fraction, following_lines):
                violations.append(
                    Violation(
                        "heading-too-low",
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
            if page_top_overcompensated(
                location, pages, normal_top, line_heights[location.page - 1]
            ):
                violations.append(
                    Violation(
                        "page-top-overcompensation",
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

        if nested_list_group is not None and heading_locations:
            first_item, block_end = nested_list_group
            group_lines = [line_tokens(lines[item]) for item in range(first_item, block_end)]
            group_lines = [item for item in group_lines if item]
            end_locations = locate(pages, anchor_windows(group_lines[-1], True)) if group_lines else []
            if not end_locations:
                unresolved.append(
                    Unresolved("heading-first-nested-item", index + 1, block_end, label, "nested item end")
                )
            elif heading_locations[0].page != end_locations[0].page:
                violations.append(
                    Violation(
                        "heading-first-nested-item",
                        index + 1,
                        block_end,
                        label,
                        heading_locations[0].page,
                        end_locations[0].page,
                    )
                )

        if media_block is not None and heading_locations:
            media_index, block_end = media_block
            block_token_lines = [line_tokens(lines[item]) for item in range(media_index + 1, block_end)]
            block_token_lines = [item for item in block_token_lines if item]
            block_end_locations = (
                locate(pages, anchor_windows(block_token_lines[-1], True))
                if block_token_lines
                else []
            )
            next_tokens = next_anchor_tokens(lines, block_end, literal)
            next_locations = locate(pages, anchor_windows(next_tokens, False)) if next_tokens else []
            upper = heading_locations[0]
            lower = block_end_locations[0] if block_end_locations else (next_locations[0] if next_locations else None)
            mapped_media = [
                item
                for item in pdf_image_locations
                if position(upper) < position(item) and (lower is None or position(item) < position(lower))
            ]
            if not mapped_media:
                unresolved.append(
                    Unresolved("heading-media-group", index + 1, block_end, label, "raster image mapping")
                )
            else:
                governed_media_end = max(mapped_media + block_end_locations, key=position)
                if upper.page != governed_media_end.page:
                    violations.append(
                        Violation(
                            "heading-media-group",
                            index + 1,
                            block_end,
                            label,
                            upper.page,
                            governed_media_end.page,
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
