#!/usr/bin/env python3
"""List, remove, insert, resize, and merge Obsidian PDF spacer blocks."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


QUOTE_RE = re.compile(r"^(\s*(?:>\s*)+)")
QUOTE_MARKER_RE = re.compile(r"^(?:[ \t]*>[ \t]?)+")
BACKSLASH_RE = re.compile(r"\\+")
FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")


@dataclass(frozen=True)
class TextFile:
    path: Path
    lines: list[str]
    endings: list[str]
    newline: str
    bom: bool
    final_newline: bool


def read_text_file(path: Path) -> TextFile:
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    parts = re.split(r"(\r\n|\n|\r)", text)
    lines: list[str] = []
    endings: list[str] = []
    for index in range(0, len(parts), 2):
        content = parts[index]
        ending = parts[index + 1] if index + 1 < len(parts) else ""
        if index == len(parts) - 1 and content == "":
            continue
        lines.append(content)
        endings.append(ending)
    newline = next((ending for ending in endings if ending), "\n")
    return TextFile(path, lines, endings, newline, bom, bool(endings and endings[-1]))


def write_text_file(source: TextFile, lines: list[str]) -> None:
    source_literal = fenced_code_lines(source.lines)
    content_endings: list[str] = []
    index = 0
    while index < len(source.lines):
        legacy = legacy_block_at(source.lines, index, source_literal)
        if legacy:
            index, _ = legacy
            continue
        content_endings.append(source.endings[index])
        index += 1

    output_endings = [source.newline] * len(lines)
    target_literal = fenced_code_lines(lines)
    content_index = 0
    mapped = [False] * len(lines)
    index = 0
    while index < len(lines):
        legacy = legacy_block_at(lines, index, target_literal)
        if legacy:
            index, _ = legacy
            continue
        if content_index < len(content_endings):
            output_endings[index] = content_endings[content_index]
            content_index += 1
            mapped[index] = True
        index += 1

    if lines:
        last_content = next(
            (index for index in range(len(lines) - 1, -1, -1) if mapped[index]),
            None,
        )
        # The original last content line may no longer be last when a spacer
        # follows it; restore the separator it originally had before the spacer.
        if (
            last_content is not None
            and last_content != len(lines) - 1
            and not output_endings[last_content]
        ):
            output_endings[last_content] = source.newline
        # Only formatter-owned spacer lines take the file-level final-newline
        # setting; mapped content lines keep their original ending.
        if not mapped[-1]:
            output_endings[-1] = source.newline if source.final_newline else ""
    text = "".join(line + ending for line, ending in zip(lines, output_endings))
    data = text.encode("utf-8")
    if source.bom:
        data = b"\xef\xbb\xbf" + data
    handle, temporary = tempfile.mkstemp(
        prefix=f".{source.path.name}.", suffix=".tmp", dir=source.path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, source.path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def strip_quote(line: str) -> str:
    return re.sub(r"^\s*(?:>\s*)+", "", line).strip()


def indented_code_line(line: str) -> bool:
    """Return whether a line starts at Markdown's indented-code depth."""
    if line.startswith("\t") or line.startswith("    "):
        return True
    content = QUOTE_MARKER_RE.sub("", line, count=1)
    return content.startswith("\t") or content.startswith("    ")


def next_fence_state(
    line: str, open_fence: tuple[str, int] | None
) -> tuple[str, int] | None:
    """Return the fence open after `line`, or None when the line closes it."""
    content = QUOTE_RE.sub("", line)
    match = FENCE_RE.match(content)
    if open_fence is None:
        if not match:
            return None
        return match.group(1)[0], len(match.group(1))
    if (
        match
        and match.group(1)[0] == open_fence[0]
        and len(match.group(1)) >= open_fence[1]
        and not match.group(2).strip()
    ):
        return None
    return open_fence


def fenced_code_lines(lines: list[str]) -> list[bool]:
    """Mark lines inside fenced code so fenced examples are never edited."""
    mask = [False] * len(lines)
    open_fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        was_open = open_fence is not None
        open_fence = next_fence_state(line, open_fence)
        mask[index] = was_open or open_fence is not None
    return mask


def fence_is_open_before(lines: list[str], index: int) -> bool:
    """Return whether the boundary before `index` is inside a fenced block."""
    open_fence: tuple[str, int] | None = None
    for line in lines[:index]:
        open_fence = next_fence_state(line, open_fence)
    return open_fence is not None


def legacy_block_at(
    lines: list[str], index: int, literal: list[bool] | None = None
) -> tuple[int, int] | None:
    """Return (end-exclusive, literal backslash count) for a legacy spacer."""
    if literal is None:
        literal = fenced_code_lines(lines)
    if index + 4 >= len(lines) or any(literal[index : index + 5]):
        return None
    if all(indented_code_line(lines[index + offset]) for offset in range(5)):
        return None
    if strip_quote(lines[index]) != "$$":
        return None
    if strip_quote(lines[index + 1]) != r"\begin{align}":
        return None
    body = strip_quote(lines[index + 2])
    if not BACKSLASH_RE.fullmatch(body):
        return None
    if strip_quote(lines[index + 3]) != r"\end{align}":
        return None
    if strip_quote(lines[index + 4]) != "$$":
        return None
    return index + 5, len(body)


def quote_prefix(line: str) -> str:
    """Return the indentation/block-quote prefix preceding visible content."""
    match = QUOTE_RE.match(line)
    return match.group(1) if match else ""


def merge_adjacent_legacy_blocks(lines: list[str]) -> tuple[list[str], int, int]:
    """Combine compatible whitespace-only align blocks separated only by blanks.

    Returns the updated lines, number of groups changed, and number of surplus
    blocks removed. Literal backslash counts are added so the merged spacer
    retains their combined height. Blank lines between blocks are retained.
    """
    updated = list(lines)
    literal = fenced_code_lines(updated)
    groups_changed = 0
    blocks_removed = 0
    index = 0
    while index < len(updated):
        first = legacy_block_at(updated, index, literal)
        if not first:
            index += 1
            continue

        first_end, total_backslashes = first
        prefix = quote_prefix(updated[index + 2])
        group_blocks = [(index, first_end)]
        cursor = first_end

        while True:
            next_start = cursor
            while next_start < len(updated) and not updated[next_start].strip():
                next_start += 1
            following = legacy_block_at(updated, next_start, literal)
            if not following or quote_prefix(updated[next_start + 2]) != prefix:
                break
            group_blocks.append((next_start, following[0]))
            total_backslashes += following[1]
            cursor = following[0]

        if len(group_blocks) > 1:
            updated[index + 2] = prefix + ("\\" * total_backslashes)
            for start, end in reversed(group_blocks[1:]):
                del updated[start:end]
                del literal[start:end]
            groups_changed += 1
            blocks_removed += len(group_blocks) - 1
        index = first_end

    return updated, groups_changed, blocks_removed


def remove_legacy_blocks(lines: list[str]) -> tuple[list[str], int]:
    output: list[str] = []
    count = 0
    index = 0
    literal = fenced_code_lines(lines)
    while index < len(lines):
        legacy = legacy_block_at(lines, index, literal)
        if legacy:
            index, _ = legacy
            count += 1
            continue
        output.append(lines[index])
        index += 1
    return output, count


def previous_nonblank(lines: list[str], index: int) -> int:
    while index > 0 and not lines[index - 1].strip():
        index -= 1
    return index


def legacy_group_before(
    lines: list[str], anchor: int
) -> list[tuple[int, int, int]]:
    """Return adjacent legacy blocks before an anchor, nearest block last.

    Blocks may be separated only by blank lines and must share the same
    indentation/block-quote prefix. This mirrors the page-break grouping used
    by merge-adjacent and keeps both resize and removal linear in the file and
    in the number of blocks in the group.
    """
    group: list[tuple[int, int, int]] = []
    literal = fenced_code_lines(lines)
    cursor = previous_nonblank(lines, anchor)
    prefix: str | None = None
    while cursor >= 5:
        start = cursor - 5
        legacy = legacy_block_at(lines, start, literal)
        if not legacy or legacy[0] != cursor:
            break
        block_prefix = quote_prefix(lines[start + 2])
        if prefix is None:
            prefix = block_prefix
        elif block_prefix != prefix:
            break
        group.append((start, cursor, legacy[1]))
        cursor = previous_nonblank(lines, start)
    group.reverse()
    return group


def command_list(args: argparse.Namespace) -> int:
    source = read_text_file(args.file)
    literal = fenced_code_lines(source.lines)
    count = 0
    index = 0
    while index < len(source.lines):
        legacy = legacy_block_at(source.lines, index, literal)
        if legacy:
            end, backslashes = legacy
            units = backslashes // 2 if backslashes % 2 == 0 else "non-paired"
            print(
                f"spacer  lines {index + 1}-{end}: "
                f"units={units} literal_backslashes={backslashes}"
            )
            count += 1
            index = end
        else:
            index += 1
    print(f"summary: spacers={count}")
    return 0


def command_clean(args: argparse.Namespace) -> int:
    source = read_text_file(args.file)
    updated, removed = remove_legacy_blocks(source.lines)
    changed = updated != source.lines
    if changed:
        write_text_file(source, updated)
    print(f"removed spacers={removed}; changed={changed}")
    return 0


def command_merge_adjacent(args: argparse.Namespace) -> int:
    source = read_text_file(args.file)
    updated, groups_changed, blocks_removed = merge_adjacent_legacy_blocks(source.lines)
    changed = updated != source.lines
    if changed:
        write_text_file(source, updated)
    print(
        f"merged groups={groups_changed} removed_blocks={blocks_removed}; "
        f"changed={changed}"
    )
    return 0


def immutable_content_text(
    source: TextFile, literal: list[bool] | None = None
) -> str:
    """Return source text with spacer blocks removed and endings preserved."""
    if literal is None:
        literal = fenced_code_lines(source.lines)
    output: list[str] = []
    index = 0
    while index < len(source.lines):
        legacy = legacy_block_at(source.lines, index, literal)
        if legacy:
            index, _ = legacy
            continue
        output.append(source.lines[index] + source.endings[index])
        index += 1
    return "".join(output)


def command_content_hash(args: argparse.Namespace) -> int:
    """Hash immutable source content and list its headings for edit verification."""
    source = read_text_file(args.file)
    literal = fenced_code_lines(source.lines)
    text = immutable_content_text(source, literal)
    data = text.encode("utf-8")
    if source.bom:
        data = b"\xef\xbb\xbf" + data
    digest = hashlib.sha256(data).hexdigest()

    headings: list[str] = []
    index = 0
    while index < len(source.lines):
        legacy = legacy_block_at(source.lines, index, literal)
        if legacy:
            index, _ = legacy
            continue
        if not literal[index] and re.match(
            r"^#{1,6}\s+\S", strip_quote(source.lines[index])
        ):
            headings.append(source.lines[index])
        index += 1

    print(f"sha256={digest}")
    print(f"headings={len(headings)}")
    for heading in headings:
        print(heading)
    return 0


def resolve_anchor(lines: list[str], before_line: int | None, before_text: str | None) -> int:
    if before_line is not None:
        if not 1 <= before_line <= len(lines) + 1:
            raise ValueError(f"--before-line must be between 1 and {len(lines) + 1}")
        return before_line - 1
    assert before_text is not None
    matches = [index for index, line in enumerate(lines) if before_text in line]
    if len(matches) != 1:
        raise ValueError(f"--before-text must match exactly once; found {len(matches)} matches")
    return matches[0]


def command_insert(args: argparse.Namespace) -> int:
    source = read_text_file(args.file)
    anchor = resolve_anchor(source.lines, args.before_line, args.before_text)
    if fence_is_open_before(source.lines, anchor):
        raise ValueError("Refusing to insert a spacer inside a fenced code block")
    target = source.lines[anchor] if anchor < len(source.lines) else ""
    if args.outside_blockquote:
        prefix = ""
    elif args.quote_prefix is not None:
        prefix = args.quote_prefix
    else:
        match = QUOTE_RE.match(target)
        prefix = match.group(1) if match else ""

    body = r"\\" * args.lines
    block = [
        f"{prefix}$$",
        f"{prefix}\\begin{{align}}",
        f"{prefix}{body}",
        f"{prefix}\\end{{align}}",
        f"{prefix}$$",
    ]

    updated = source.lines[:anchor] + block + source.lines[anchor:]
    updated, groups_changed, _ = merge_adjacent_legacy_blocks(updated)
    write_text_file(source, updated)
    print(
        f"inserted units={args.lines} before line {anchor + 1}; "
        f"adjacent_groups_merged={groups_changed}"
    )
    return 0


def command_set_legacy_before(args: argparse.Namespace) -> int:
    """Resize the annotation-free spacer group immediately before a target."""
    source = read_text_file(args.file)
    anchor = resolve_anchor(source.lines, args.before_line, args.before_text)
    group = legacy_group_before(source.lines, anchor)
    if not group:
        raise ValueError("No annotation-free spacer was found immediately before target")

    start = group[0][0]
    previous_backslashes = sum(block[2] for block in group)
    previous_units = previous_backslashes / 2
    updated = list(source.lines)
    prefix = quote_prefix(updated[start + 2])
    updated[start + 2] = prefix + (r"\\" * args.lines)
    for block_start, block_end, _ in reversed(group[1:]):
        del updated[block_start:block_end]
    write_text_file(source, updated)
    print(
        f"updated spacer previous_units={previous_units:g} "
        f"previous_chunks={len(group)} units={args.lines} before line {anchor + 1}"
    )
    return 0


def command_remove_legacy_before(args: argparse.Namespace) -> int:
    """Remove only the adjacent annotation-free spacer group before a target."""
    source = read_text_file(args.file)
    anchor = resolve_anchor(source.lines, args.before_line, args.before_text)
    group = legacy_group_before(source.lines, anchor)
    if not group:
        raise ValueError("No annotation-free spacer was found immediately before target")

    previous_units = sum(block[2] for block in group) / 2
    updated = list(source.lines)
    for block_start, block_end, _ in reversed(group):
        del updated[block_start:block_end]
    write_text_file(source, updated)
    print(
        f"removed spacer previous_units={previous_units:g} "
        f"previous_chunks={len(group)} before line {anchor + 1}"
    )
    return 0


def path_arg(value: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Markdown file not found: {value}")
    if path.suffix.lower() != ".md":
        raise argparse.ArgumentTypeError("Target must be a .md file")
    return path


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list annotation-free spacer blocks")
    list_parser.add_argument("file", type=path_arg)
    list_parser.set_defaults(handler=command_list)

    hash_parser = subparsers.add_parser(
        "content-hash", help="hash non-spacer source content and list all headings"
    )
    hash_parser.add_argument("file", type=path_arg)
    hash_parser.set_defaults(handler=command_content_hash)

    clean_parser = subparsers.add_parser(
        "clean", help="remove annotation-free spacer blocks"
    )
    clean_parser.add_argument("file", type=path_arg)
    clean_parser.set_defaults(handler=command_clean)

    merge_parser = subparsers.add_parser(
        "merge-adjacent",
        help="combine adjacent compatible whitespace-only align blocks",
    )
    merge_parser.add_argument("file", type=path_arg)
    merge_parser.set_defaults(handler=command_merge_adjacent)

    insert_parser = subparsers.add_parser("insert", help="insert a spacer")
    insert_parser.add_argument("file", type=path_arg)
    anchor = insert_parser.add_mutually_exclusive_group(required=True)
    anchor.add_argument("--before-line", type=int)
    anchor.add_argument("--before-text")
    insert_parser.add_argument(
        "--lines", type=positive_int, required=True, metavar="N"
    )
    quote_handling = insert_parser.add_mutually_exclusive_group()
    quote_handling.add_argument(
        "--quote-prefix", help="override the blockquote prefix inherited from the target line"
    )
    quote_handling.add_argument(
        "--outside-blockquote",
        action="store_true",
        help="insert an unquoted spacer before a blockquoted target such as a callout header",
    )
    insert_parser.set_defaults(handler=command_insert)

    legacy_set_parser = subparsers.add_parser(
        "set-legacy-before", help="resize the annotation-free spacer before a target"
    )
    legacy_set_parser.add_argument("file", type=path_arg)
    legacy_anchor = legacy_set_parser.add_mutually_exclusive_group(required=True)
    legacy_anchor.add_argument("--before-line", type=int)
    legacy_anchor.add_argument("--before-text")
    legacy_set_parser.add_argument(
        "--lines", type=positive_int, required=True, metavar="N"
    )
    legacy_set_parser.set_defaults(handler=command_set_legacy_before)

    legacy_remove_parser = subparsers.add_parser(
        "remove-legacy-before",
        help="remove the annotation-free spacer immediately before a target",
    )
    legacy_remove_parser.add_argument("file", type=path_arg)
    legacy_remove_anchor = legacy_remove_parser.add_mutually_exclusive_group(required=True)
    legacy_remove_anchor.add_argument("--before-line", type=int)
    legacy_remove_anchor.add_argument("--before-text")
    legacy_remove_parser.set_defaults(handler=command_remove_legacy_before)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
