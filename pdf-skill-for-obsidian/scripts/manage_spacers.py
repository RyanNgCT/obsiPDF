#!/usr/bin/env python3
"""List, remove, and insert managed Obsidian PDF spacer blocks."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


START_TOKEN = "obsidian-pdf-formatter:start"
END_TOKEN = "obsidian-pdf-formatter:end"
START_RE = re.compile(rf"^\s*(?:>\s*)*<!--\s*{re.escape(START_TOKEN)}\b")
END_RE = re.compile(rf"^\s*(?:>\s*)*<!--\s*{re.escape(END_TOKEN)}\s*-->")
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
            print(f"legacy  lines {index + 1}-{end}: backslashes={backslashes}")
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

    blank = quote_blank(prefix)
    body = r"\\" * args.lines
    block = [
        f'{prefix}<!-- {START_TOKEN} id="{args.id}" lines="{args.lines}" -->',
        blank,
        f"{prefix}$$",
        f"{prefix}\\begin{{align}}",
        f"{prefix}{body}",
        f"{prefix}\\end{{align}}",
        f"{prefix}$$",
        blank,
        f"{prefix}<!-- {END_TOKEN} -->",
    ]

    updated = source.lines[:anchor] + block + source.lines[anchor:]
    write_text_file(source, updated)
    print(f"inserted id={args.id} units={args.lines} before line {anchor + 1}")
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

    clean_parser = subparsers.add_parser("clean", help="remove managed spacer blocks")
    clean_parser.add_argument("file", type=path_arg)
    clean_parser.add_argument(
        "--include-legacy", action="store_true", help="also remove whitespace-only align blocks"
    )
    clean_parser.set_defaults(handler=command_clean)

    insert_parser = subparsers.add_parser("insert", help="insert a managed spacer")
    insert_parser.add_argument("file", type=path_arg)
    anchor = insert_parser.add_mutually_exclusive_group(required=True)
    anchor.add_argument("--before-line", type=int)
    anchor.add_argument("--before-text")
    insert_parser.add_argument(
        "--lines", type=int, required=True, choices=range(1, 101), metavar="1..100"
    )
    insert_parser.add_argument("--id", type=safe_id, required=True)
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
