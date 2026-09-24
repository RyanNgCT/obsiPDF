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
    def test_h2_through_h5_accept_review_band_with_meaningful_content(self) -> None:
        for level in range(2, 6):
            self.assertFalse(analyze_layout.heading_is_too_low(level, 0.8499, 0))
            self.assertTrue(analyze_layout.heading_is_too_low(level, 0.85, 1))
            self.assertFalse(analyze_layout.heading_is_too_low(level, 0.85, 2))
            self.assertFalse(analyze_layout.heading_is_too_low(level, 0.8999, 3))

    def test_bottom_ten_percent_remains_a_hard_boundary(self) -> None:
        for level in range(2, 6):
            self.assertTrue(analyze_layout.heading_is_too_low(level, 0.90, 10))

    def test_h1_and_h6_are_not_forced_by_low_heading_rule(self) -> None:
        self.assertFalse(analyze_layout.heading_is_too_low(1, 0.99, 0))
        self.assertFalse(analyze_layout.heading_is_too_low(6, 0.99, 0))

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

    def test_unquoted_media_after_callout_is_not_part_of_callout(self) -> None:
        lines = (
            "> [!note] Standalone components\n"
            "> The callout body is complete here.\n\n"
            "![diagram](diagram.png)\n\n"
            "- Explanation"
        ).splitlines()
        callout = analyze_layout.collect_callouts(lines)[0]
        self.assertEqual(callout.lines, lines[:2])
        self.assertEqual(callout.end, 2)


class MediaParsingTests(unittest.TestCase):
    def test_markdown_and_wikilink_images_are_recognized(self) -> None:
        self.assertEqual(
            analyze_layout.markdown_image_target("> ![diagram|450](../assets/chart.jpg)"),
            "../assets/chart.jpg",
        )
        self.assertEqual(
            analyze_layout.markdown_image_target("> ![[chart.png|450]]"), "chart.png"
        )

    def test_media_led_block_includes_accompanying_spaced_list(self) -> None:
        lines = (
            "#### Example\n![diagram](diagram.png)\n1. First step\n\n"
            "2. Second step\n  - detail\n\n> [!success] Next block"
        ).splitlines()
        self.assertEqual(analyze_layout.media_led_block(lines, 1), (1, 6))

    def test_media_led_block_without_list_contains_image_only(self) -> None:
        lines = "#### Diagram\n![diagram](diagram.png)\n\nNext paragraph".splitlines()
        self.assertEqual(analyze_layout.media_led_block(lines, 1), (1, 2))


class CalloutAnchorTests(unittest.TestCase):
    def test_short_header_is_resolved_before_body_on_next_page(self) -> None:
        pages = [
            analyze_layout.PageData(["bottom", "up", "design"], [790.0] * 3, 842.0, 790.0),
            analyze_layout.PageData(["bottom", "up", "design", "involves"], [50.0] * 4, 842.0, 50.0),
        ]
        boundary = analyze_layout.Location(2, 50.0, 842.0)
        found = analyze_layout.resolve_short_anchor(pages, ["bottom", "up", "design"], boundary)
        self.assertEqual((found[0].page, found[0].top), (1, 790.0))


class OvercompensationTests(unittest.TestCase):
    def test_first_heading_far_below_normal_top_is_rejected(self) -> None:
        pages = [
            analyze_layout.PageData(["normal"], [55.0], 842.0, 55.0),
            analyze_layout.PageData(["dimension"], [230.0], 842.0, 230.0),
        ]
        self.assertTrue(
            analyze_layout.page_top_overcompensated(
                analyze_layout.Location(2, 230.0, 842.0), pages
            )
        )

    def test_target_at_normal_top_is_accepted(self) -> None:
        pages = [
            analyze_layout.PageData(["normal"], [55.0], 842.0, 55.0),
            analyze_layout.PageData(["heading"], [72.0], 842.0, 72.0),
        ]
        self.assertFalse(
            analyze_layout.page_top_overcompensated(
                analyze_layout.Location(2, 72.0, 842.0), pages
            )
        )

    def test_punctuation_only_content_above_target_prevents_false_positive(self) -> None:
        pages = [
            analyze_layout.PageData(["normal"], [55.0], 842.0, 55.0),
            analyze_layout.PageData(["criteria"], [66.0], 842.0, 24.0),
        ]
        self.assertFalse(
            analyze_layout.page_top_overcompensated(
                analyze_layout.Location(2, 66.0, 842.0), pages
            )
        )


if __name__ == "__main__":
    unittest.main()
