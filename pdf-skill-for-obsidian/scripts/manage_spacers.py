#!/usr/bin/env python3
"""List, remove, insert, resize, and normalize Obsidian PDF spacer blocks."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


START_TOKEN = "obsidian-pdf-formatter:start"
END_TOKEN = "obsidian-pdf-formatter:end"
START_RE = re.compile(
    rf"^\s*(?:>\s*)*(?:<!--\s*|%%\s*){re.escape(START_TOKEN)}\b"
)
END_RE = re.compile(
    rf"^\s*(?:>\s*)*(?:<!--\s*|%%\s*){re.escape(END_TOKEN)}(?:\s*-->|\s*%%)"
)
ID_RE = re.compile(r'\bid="([^"]+)"')
LINES_RE = re.compile(r'\blines="(\d+)"')
QUOTE_RE = re.compile(r"^(\s*(?:>\s*)+)")
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class TextFile:
    path: Path
    lines: list[str]
    newline: str
    bom: bool
    final_newline: bool


def read_text_file(path: Path) -> TextFile:
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    return TextFile(path, text.splitlines(), newline, bom, text.endswith(("\n", "\r")))


def write_text_file(source: TextFile, lines: list[str]) -> None:
    text = source.newline.join(lines)
    if source.final_newline:
        text += source.newline
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


def legacy_block_at(lines: list[str], index: int) -> tuple[int, int] | None:
    """Return (end-exclusive, literal backslash count) for a legacy spacer."""
    if index + 4 >= len(lines) or strip_quote(lines[index]) != "$$":
        return None
    if strip_quote(lines[index + 1]) != r"\begin{align}":
        return None
    body = strip_quote(lines[index + 2])
    if not re.fullmatch(r"\\+", body):
        return None
    if strip_quote(lines[index + 3]) != r"\end{align}":
        return None
    if strip_quote(lines[index + 4]) != "$$":
        return None
    return index + 5, len(body)


def quote_prefix(line: str) -> str:
    """Return the indentation/block-quote prefix preceding visible content."""
    match = re.match(r"^(\s*(?:>\s*)*)", line)
    return match.group(1) if match else ""


def merge_adjacent_legacy_blocks(lines: list[str]) -> tuple[list[str], int, int]:
    """Combine compatible whitespace-only align blocks separated only by blanks.

    Returns the updated lines, number of groups changed, and number of surplus
    blocks removed. Literal backslash counts are added exactly so pagination
    height is preserved.
    """
    updated = list(lines)
    groups_changed = 0
    blocks_removed = 0
    index = 0
    while index < len(updated):
        first = legacy_block_at(updated, index)
        if not first:
            index += 1
            continue

        first_end, total_backslashes = first
        prefix = quote_prefix(updated[index + 2])
        group_end = first_end
        group_blocks = 1

        while True:
            next_start = group_end
            while next_start < len(updated) and not updated[next_start].strip():
                next_start += 1
            following = legacy_block_at(updated, next_start)
            if not following or quote_prefix(updated[next_start + 2]) != prefix:
                break
            group_end, backslashes = following
            total_backslashes += backslashes
            group_blocks += 1

        if group_blocks > 1:
            updated[index + 2] = prefix + ("\\" * total_backslashes)
            del updated[first_end:group_end]
            groups_changed += 1
            blocks_removed += group_blocks - 1
            index = first_end
        else:
            index = first_end

    return updated, groups_changed, blocks_removed


def managed_blocks(lines: list[str]) -> list[tuple[int, int, str, int | None]]:
    blocks: list[tuple[int, int, str, int | None]] = []
    index = 0
    while index < len(lines):
        if not START_RE.match(lines[index]):
            index += 1
            continue
        start = index
        id_match = ID_RE.search(lines[index])
        identifier = id_match.group(1) if id_match else "unknown"
        line_match = LINES_RE.search(lines[index])
        units = int(line_match.group(1)) if line_match else None
        index += 1
        while index < len(lines) and not END_RE.match(lines[index]):
            index += 1
        if index >= len(lines):
            raise ValueError(f"Unclosed managed spacer beginning at line {start + 1}")
        blocks.append((start, index + 1, identifier, units))
        index += 1
    return blocks


def remove_blocks(lines: list[str], include_legacy: bool) -> tuple[list[str], int, int]:
    output: list[str] = []
    managed_count = 0
    legacy_count = 0
    index = 0
    while index < len(lines):
        if START_RE.match(lines[index]):
            managed_count += 1
            index += 1
            while index < len(lines) and not END_RE.match(lines[index]):
                index += 1
            if index >= len(lines):
                raise ValueError("Unclosed managed spacer block")
            index += 1
            continue
        legacy = legacy_block_at(lines, index) if include_legacy else None
        if legacy:
            index, _ = legacy
            legacy_count += 1
            continue
        output.append(lines[index])
        index += 1

    return output, managed_count, legacy_count


def command_list(args: argparse.Namespace) -> int:
    source = read_text_file(args.file)
    found = managed_blocks(source.lines)
    for start, end, identifier, units in found:
        shown_units = units if units is not None else "unknown"
        print(f"managed lines {start + 1}-{end}: id={identifier} units={shown_units}")

    legacy_count = 0
    index = 0
    while index < len(source.lines):
        if START_RE.match(source.lines[index]):
            index += 1
            while index < len(source.lines) and not END_RE.match(source.lines[index]):
                index += 1
            index += 1
            continue
        legacy = legacy_block_at(source.lines, index)
        if legacy:
            end, backslashes = legacy
            units = backslashes // 2 if backslashes % 2 == 0 else "non-paired"
            print(
                f"legacy  lines {index + 1}-{end}: "
                f"units={units} literal_backslashes={backslashes}"
            )
            legacy_count += 1
            index = end
        else:
            index += 1
    print(f"summary: managed={len(found)} legacy={legacy_count}")
    return 0


def command_clean(args: argparse.Namespace) -> int:
    source = read_text_file(args.file)
    updated, managed_count, legacy_count = remove_blocks(source.lines, args.include_legacy)
    if updated != source.lines:
        write_text_file(source, updated)
    print(f"removed managed={managed_count} legacy={legacy_count}; changed={updated != source.lines}")
    return 0


def command_merge_adjacent(args: argparse.Namespace) -> int:
    source = read_text_file(args.file)
    updated, groups_changed, blocks_removed = merge_adjacent_legacy_blocks(source.lines)
    if updated != source.lines:
        write_text_file(source, updated)
    print(
        f"merged groups={groups_changed} removed_blocks={blocks_removed}; "
        f"changed={updated != source.lines}"
    )
    return 0


def immutable_content_lines(lines: list[str]) -> list[str]:
    """Return all user content while excluding only formatter metadata/spacers."""
    output: list[str] = []
    index = 0
    while index < len(lines):
        if START_RE.match(lines[index]) or END_RE.match(lines[index]):
            index += 1
            continue
        legacy = legacy_block_at(lines, index)
        if legacy:
            index, _ = legacy
            continue
        if lines[index].strip():
            output.append(lines[index])
        index += 1
    return output


def command_content_hash(args: argparse.Namespace) -> int:
    """Hash immutable source content and list its headings for edit verification."""
    source = read_text_file(args.file)
    lines = immutable_content_lines(source.lines)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    headings = [line for line in lines if re.match(r"^#{1,6}\s+\S", line)]
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


def quote_blank(prefix: str) -> str:
    return prefix.rstrip() if prefix else ""


def command_insert(args: argparse.Namespace) -> int:
    source = read_text_file(args.file)
    existing = managed_blocks(source.lines)
    if any(identifier == args.id for _, _, identifier, _ in existing):
        raise ValueError(f"Managed spacer id already exists: {args.id}")
    anchor = resolve_anchor(source.lines, args.before_line, args.before_text)
    target = source.lines[anchor] if anchor < len(source.lines) else ""
    if args.outside_blockquote:
        prefix = ""
    elif args.quote_prefix is not None:
        prefix = args.quote_prefix
    else:
        match = QUOTE_RE.match(target)
        prefix = match.group(1) if match else ""

    body = r"\\" * args.lines
    latex = [
        f"{prefix}$$",
        f"{prefix}\\begin{{align}}",
        f"{prefix}{body}",
        f"{prefix}\\end{{align}}",
        f"{prefix}$$",
    ]
    block = latex if args.no_markers else [
        f'{prefix}%% {START_TOKEN} id="{args.id}" lines="{args.lines}" %%',
        *latex,
        f"{prefix}%% {END_TOKEN} %%",
    ]

    updated = source.lines[:anchor] + block + source.lines[anchor:]
    groups_changed = 0
    if args.no_markers:
        updated, groups_changed, _ = merge_adjacent_legacy_blocks(updated)
    write_text_file(source, updated)
    print(
        f"inserted id={args.id} units={args.lines} before line {anchor + 1}; "
        f"adjacent_groups_merged={groups_changed}"
    )
    return 0


def command_set(args: argparse.Namespace) -> int:
    source = read_text_file(args.file)
    matches = [block for block in managed_blocks(source.lines) if block[2] == args.id]
    if len(matches) != 1:
        raise ValueError(f"Managed spacer id must exist exactly once; found {len(matches)}")
    start, end, _, _ = matches[0]
    updated = list(source.lines)
    start_prefix = re.match(r"^(\s*(?:>\s*)*)", updated[start]).group(1)
    end_prefix = re.match(r"^(\s*(?:>\s*)*)", updated[end - 1]).group(1)
    updated[start] = (
        f'{start_prefix}%% {START_TOKEN} id="{args.id}" lines="{args.lines}" %%'
    )
    updated[end - 1] = f"{end_prefix}%% {END_TOKEN} %%"
    body_index = None
    for index in range(start + 1, end - 1):
        stripped = strip_quote(updated[index])
        if re.fullmatch(r"\\+", stripped):
            body_index = index
            break
    if body_index is None:
        raise ValueError(f"Managed spacer body was not found for id: {args.id}")
    prefix_match = re.match(r"^(\s*(?:>\s*)*)", updated[body_index])
    prefix = prefix_match.group(1) if prefix_match else ""
    updated[body_index] = prefix + (r"\\" * args.lines)
    write_text_file(source, updated)
    print(f"updated id={args.id} units={args.lines}")
    return 0


def command_set_legacy_before(args: argparse.Namespace) -> int:
    """Resize the annotation-free legacy spacer immediately before a target."""
    source = read_text_file(args.file)
    anchor = resolve_anchor(source.lines, args.before_line, args.before_text)
    candidates: list[tuple[int, int, int]] = []
    index = 0
    while index < anchor:
        legacy = legacy_block_at(source.lines, index)
        if not legacy:
            index += 1
            continue
        end, _ = legacy
        between = source.lines[end:anchor]
        if all(not line.strip() for line in between):
            candidates.append((index, end, legacy[1]))
        index = end
    if not candidates:
        raise ValueError("No annotation-free legacy spacer was found immediately before target")

    group = [candidates[-1]]
    group_start = candidates[-1][0]
    group_prefix = quote_prefix(source.lines[group_start + 2])
    while True:
        prior = None
        scan = 0
        while scan < group_start:
            legacy = legacy_block_at(source.lines, scan)
            if not legacy:
                scan += 1
                continue
            end, backslashes = legacy
            if (
                quote_prefix(source.lines[scan + 2]) == group_prefix
                and all(not line.strip() for line in source.lines[end:group_start])
            ):
                prior = (scan, end, backslashes)
            scan = end
        if prior is None:
            break
        group.insert(0, prior)
        group_start = prior[0]

    start = group[0][0]
    end = group[-1][1]
    previous_backslashes = sum(block[2] for block in group)
    previous_units = previous_backslashes / 2
    updated = list(source.lines)
    prefix = quote_prefix(updated[start + 2])
    replacement = updated[start : start + 5]
    replacement[2] = prefix + (r"\\" * args.lines)
    updated[start:end] = replacement
    write_text_file(source, updated)
    print(
        f"updated legacy spacer previous_units={previous_units:g} "
        f"previous_chunks={len(group)} units={args.lines} before line {anchor + 1}"
    )
    return 0


def command_remove_legacy_before(args: argparse.Namespace) -> int:
    """Remove only the adjacent annotation-free spacer group before a target."""
    source = read_text_file(args.file)
    anchor = resolve_anchor(source.lines, args.before_line, args.before_text)
    blocks: list[tuple[int, int, int]] = []
    index = 0
    while index < anchor:
        legacy = legacy_block_at(source.lines, index)
        if not legacy:
            index += 1
            continue
        end, backslashes = legacy
        blocks.append((index, end, backslashes))
        index = end

    if not blocks:
        raise ValueError("No annotation-free legacy spacer was found before target")

    group = [blocks[-1]]
    group_start = blocks[-1][0]
    group_prefix = quote_prefix(source.lines[group_start + 2])
    if not all(not line.strip() for line in source.lines[blocks[-1][1] : anchor]):
        raise ValueError("No annotation-free legacy spacer was found immediately before target")

    for prior in reversed(blocks[:-1]):
        if (
            quote_prefix(source.lines[prior[0] + 2]) == group_prefix
            and all(not line.strip() for line in source.lines[prior[1] : group_start])
        ):
            group.insert(0, prior)
            group_start = prior[0]
        else:
            break

    start = group[0][0]
    end = group[-1][1]
    previous_units = sum(block[2] for block in group) / 2
    updated = source.lines[:start] + source.lines[end:]
    write_text_file(source, updated)
    print(
        f"removed legacy spacer previous_units={previous_units:g} "
        f"previous_chunks={len(group)} before line {anchor + 1}"
    )
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    """Remove managed marker lines while preserving their LaTeX spacer blocks."""
    source = read_text_file(args.file)
    blocks = managed_blocks(source.lines)
    marker_lines = {index for start, end, _, _ in blocks for index in (start, end - 1)}
    updated = [line for index, line in enumerate(source.lines) if index not in marker_lines]
    if updated != source.lines:
        write_text_file(source, updated)
    print(f"removed marker pairs={len(blocks)}; changed={updated != source.lines}")
    return 0


def path_arg(value: str) -> Path:
    path = Path(value).resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"Markdown file not found: {value}")
    if path.suffix.lower() != ".md":
        raise argparse.ArgumentTypeError("Target must be a .md file")
    return path


def safe_id(value: str) -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "ID must use lowercase letters, digits, dot, underscore, or hyphen"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list managed and legacy spacer blocks")
    list_parser.add_argument("file", type=path_arg)
    list_parser.set_defaults(handler=command_list)

    hash_parser = subparsers.add_parser(
        "content-hash", help="hash non-spacer source content and list all headings"
    )
    hash_parser.add_argument("file", type=path_arg)
    hash_parser.set_defaults(handler=command_content_hash)

    clean_parser = subparsers.add_parser("clean", help="remove managed spacer blocks")
    clean_parser.add_argument("file", type=path_arg)
    clean_parser.add_argument(
        "--include-legacy", action="store_true", help="also remove whitespace-only align blocks"
    )
    clean_parser.set_defaults(handler=command_clean)

    merge_parser = subparsers.add_parser(
        "merge-adjacent",
        help="combine adjacent compatible whitespace-only align blocks",
    )
    merge_parser.add_argument("file", type=path_arg)
    merge_parser.set_defaults(handler=command_merge_adjacent)

    insert_parser = subparsers.add_parser("insert", help="insert a managed spacer")
    insert_parser.add_argument("file", type=path_arg)
    anchor = insert_parser.add_mutually_exclusive_group(required=True)
    anchor.add_argument("--before-line", type=int)
    anchor.add_argument("--before-text")
    insert_parser.add_argument(
        "--lines", type=int, required=True, choices=range(1, 101), metavar="1..100"
    )
    insert_parser.add_argument("--id", type=safe_id, required=True)
    insert_parser.add_argument(
        "--no-markers", action="store_true", help="write an annotation-free legacy spacer"
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

    set_parser = subparsers.add_parser("set", help="change the size of one managed spacer")
    set_parser.add_argument("file", type=path_arg)
    set_parser.add_argument("--lines", type=int, required=True, choices=range(1, 101), metavar="1..100")
    set_parser.add_argument("--id", type=safe_id, required=True)
    set_parser.set_defaults(handler=command_set)

    legacy_set_parser = subparsers.add_parser(
        "set-legacy-before", help="resize the annotation-free legacy spacer before a target"
    )
    legacy_set_parser.add_argument("file", type=path_arg)
    legacy_anchor = legacy_set_parser.add_mutually_exclusive_group(required=True)
    legacy_anchor.add_argument("--before-line", type=int)
    legacy_anchor.add_argument("--before-text")
    legacy_set_parser.add_argument(
        "--lines", type=int, required=True, choices=range(1, 101), metavar="1..100"
    )
    legacy_set_parser.set_defaults(handler=command_set_legacy_before)

    legacy_remove_parser = subparsers.add_parser(
        "remove-legacy-before",
        help="remove the annotation-free legacy spacer immediately before a target",
    )
    legacy_remove_parser.add_argument("file", type=path_arg)
    legacy_remove_anchor = legacy_remove_parser.add_mutually_exclusive_group(required=True)
    legacy_remove_anchor.add_argument("--before-line", type=int)
    legacy_remove_anchor.add_argument("--before-text")
    legacy_remove_parser.set_defaults(handler=command_remove_legacy_before)

    finalize_parser = subparsers.add_parser(
        "finalize", help="remove managed marker comments but keep LaTeX spacers"
    )
    finalize_parser.add_argument("file", type=path_arg)
    finalize_parser.set_defaults(handler=command_finalize)
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
