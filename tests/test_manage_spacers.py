from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "pdf-skill-for-obsidian" / "scripts" / "manage_spacers.py"
SPEC = importlib.util.spec_from_file_location("manage_spacers", SCRIPT)
assert SPEC and SPEC.loader
manage_spacers = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manage_spacers
SPEC.loader.exec_module(manage_spacers)


def spacer(prefix: str = "", units: int = 1) -> list[str]:
    return [
        f"{prefix}$$",
        f"{prefix}\\begin{{align}}",
        f"{prefix}{'\\\\' * units}",
        f"{prefix}\\end{{align}}",
        f"{prefix}$$",
    ]


class LegacySpacerTests(unittest.TestCase):
    def test_quoted_spacer_is_recognized(self) -> None:
        lines = spacer("> ")
        self.assertEqual(manage_spacers.legacy_block_at(lines, 0), (5, 2))

    def test_group_before_keeps_only_adjacent_same_prefix_blocks(self) -> None:
        lines = ["# H", "", *spacer(), "", *spacer("> "), "", "## Next"]
        anchor = len(lines) - 1
        self.assertEqual(manage_spacers.legacy_group_before(lines, anchor), [(8, 13, 2)])

    def test_remove_legacy_blocks_removes_all_spacers(self) -> None:
        lines = ["# H", "", *spacer(), "", *spacer("> "), "", "## Next"]
        updated, removed = manage_spacers.remove_legacy_blocks(lines)
        self.assertEqual(removed, 2)
        self.assertEqual(updated, ["# H", "", "", "", "## Next"])

    def test_fenced_spacer_is_not_recognized_or_removed(self) -> None:
        lines = ["```latex", *spacer(), "```", "", "Body"]
        self.assertIsNone(manage_spacers.legacy_block_at(lines, 1))
        updated, removed = manage_spacers.remove_legacy_blocks(lines)
        self.assertEqual(removed, 0)
        self.assertEqual(updated, lines)

    def test_merge_adjacent_sums_units_and_preserves_blank_lines(self) -> None:
        lines = [*spacer(), "", "", *spacer()]
        updated, groups_changed, blocks_removed = manage_spacers.merge_adjacent_legacy_blocks(lines)
        self.assertEqual((groups_changed, blocks_removed), (1, 1))
        self.assertEqual(updated, [*spacer(units=2), "", ""])

    def test_resize_and_remove_keep_blanks_between_group_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            path.write_text(
                "\n".join([*spacer(), "", "", *spacer(), "## Target"]) + "\n",
                encoding="utf-8",
            )

            manage_spacers.command_set_legacy_before(
                argparse.Namespace(
                    file=path, before_line=None, before_text="## Target", lines=3
                )
            )
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                [*spacer(units=3), "", "", "## Target"],
            )

            manage_spacers.command_remove_legacy_before(
                argparse.Namespace(file=path, before_line=None, before_text="## Target")
            )
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                ["", "", "## Target"],
            )

    def test_content_hash_keeps_blank_lines_and_quoted_headings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            path.write_bytes(
                b"# Top\n\n> ### Quoted\n\n```\n# Not a heading\n```\n"
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                manage_spacers.command_content_hash(argparse.Namespace(file=path))
            report = output.getvalue()
            self.assertIn("headings=2", report)
            self.assertIn("> ### Quoted", report)
            self.assertNotIn("# Not a heading", report)

            source = manage_spacers.read_text_file(path)
            self.assertIn("\n\n", manage_spacers.immutable_content_text(source))

    def test_mixed_line_endings_survive_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            original = b"# H\r\nline\nend\r\n"
            path.write_bytes(original)
            source = manage_spacers.read_text_file(path)
            manage_spacers.write_text_file(source, source.lines)
            self.assertEqual(path.read_bytes(), original)

    def test_clean_preserves_surrounding_mixed_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            block = "\r\n".join(spacer())
            path.write_bytes(("# H\r\n\r\n" + block + "\r\n\nText\n").encode("utf-8"))
            manage_spacers.command_clean(argparse.Namespace(file=path))
            self.assertEqual(path.read_bytes(), b"# H\r\n\r\n\nText\n")

    def test_clean_preserves_repeated_blank_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            parts = [
                ("line 0", "\r\n"),
                ("", "\n"),
                ("line 2", "\n"),
                ("", "\n"),
                ("", "\n"),
                ("", "\r\n"),
                ("", "\r\n"),
                *((line, "\n") for line in spacer(1)),
                ("", "\r\n"),
                *((line, "\n") for line in spacer(2)),
                ("", "\r\n"),
            ]
            path.write_bytes(
                "".join(line + ending for line, ending in parts).encode("utf-8")
            )
            before = manage_spacers.immutable_content_text(
                manage_spacers.read_text_file(path)
            )
            manage_spacers.command_clean(argparse.Namespace(file=path))
            after = manage_spacers.immutable_content_text(
                manage_spacers.read_text_file(path)
            )
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
