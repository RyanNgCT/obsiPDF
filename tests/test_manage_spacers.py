from __future__ import annotations

import importlib.util
import sys
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

    def test_merge_adjacent_sums_units(self) -> None:
        lines = [*spacer(), "", *spacer()]
        updated, groups_changed, blocks_removed = manage_spacers.merge_adjacent_legacy_blocks(lines)
        self.assertEqual((groups_changed, blocks_removed), (1, 1))
        self.assertEqual(updated, [*spacer(units=2)])


if __name__ == "__main__":
    unittest.main()
