from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "pdf-skill-for-obsidian" / "scripts" / "analyze_layout.py"
SPEC = importlib.util.spec_from_file_location("analyze_layout", SCRIPT)
assert SPEC and SPEC.loader
analyze_layout = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analyze_layout
SPEC.loader.exec_module(analyze_layout)


class HeadingThresholdTests(unittest.TestCase):
    def test_h2_through_h5_use_bottom_fifteen_percent(self) -> None:
        for level in range(2, 6):
            self.assertFalse(analyze_layout.heading_is_too_low(level, 0.8499))
            self.assertTrue(analyze_layout.heading_is_too_low(level, 0.85))

    def test_h1_and_h6_are_not_forced_by_low_heading_rule(self) -> None:
        self.assertFalse(analyze_layout.heading_is_too_low(1, 0.99))
        self.assertFalse(analyze_layout.heading_is_too_low(6, 0.99))

    def test_short_heading_can_be_disambiguated_before_its_content(self) -> None:
        pages = [
            analyze_layout.PageData(["issue", "trackers"], [795.0, 795.0], 842.0),
            analyze_layout.PageData(
                ["can", "use", "issue", "trackers", "issue", "trackers"],
                [80.0, 80.0, 80.0, 80.0, 200.0, 200.0],
                842.0,
            ),
        ]
        candidates = analyze_layout.locate_all(
            pages, analyze_layout.anchor_windows(["issue", "trackers"], False)
        )
        boundary = analyze_layout.Location(2, 80.0, 842.0)
        inferred = analyze_layout.nearest_location_before(candidates, boundary)
        self.assertIsNotNone(inferred)
        self.assertEqual((inferred.page, inferred.top), (1, 795.0))


class HeadingCalloutGroupingTests(unittest.TestCase):
    def associated(self, markdown: str):
        lines = markdown.splitlines()
        callouts = analyze_layout.collect_callouts(lines)
        return analyze_layout.heading_associated_callout(
            lines, 0, {callout.start: callout for callout in callouts}
        )

    def test_direct_callout_is_governed_by_heading(self) -> None:
        callout = self.associated("### Heading\n\n> [!note] Title\n> Body")
        self.assertIsNotNone(callout)
        self.assertEqual(callout.end, 4)

    def test_one_introductory_paragraph_and_callout_form_one_group(self) -> None:
        callout = self.associated(
            "### Heading\nA short introduction that wraps\nonto a second source line.\n\n"
            "> [!note] Title\n> Body"
        )
        self.assertIsNotNone(callout)

    def test_list_breaks_heading_callout_association(self) -> None:
        callout = self.associated("### Heading\n- Item\n\n> [!note] Title\n> Body")
        self.assertIsNone(callout)

    def test_second_paragraph_breaks_heading_callout_association(self) -> None:
        callout = self.associated(
            "### Heading\nFirst paragraph.\n\nSecond paragraph.\n\n> [!note] Title\n> Body"
        )
        self.assertIsNone(callout)

    def test_formatter_spacer_does_not_break_direct_association(self) -> None:
        callout = self.associated(
            "### Heading\n$$\n\\begin{align}\n\\\\\n\\end{align}\n$$\n> [!note] Title\n> Body"
        )
        self.assertIsNotNone(callout)


class MediaParsingTests(unittest.TestCase):
    def test_markdown_and_wikilink_images_are_recognized(self) -> None:
        self.assertEqual(
            analyze_layout.markdown_image_target("> ![diagram|450](../assets/chart.jpg)"),
            "../assets/chart.jpg",
        )
        self.assertEqual(
            analyze_layout.markdown_image_target("> ![[chart.png|450]]"), "chart.png"
        )


if __name__ == "__main__":
    unittest.main()
