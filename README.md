# PDF Skill for Obsidian

A reusable Codex skill for polishing the pagination of Obsidian-flavoured Markdown before PDF export by @RyanNgCT, co-authored by Codex.

The skill dynamically adds managed LaTeX whitespace so that:
- H1-H6 headings stay with the complete block they introduce, including a directly governed callout and an optional short lead-in paragraph.
- H2-H5 sections that would begin in the bottom 15% of a page are moved to the next page.
- Complete [Obsidian callouts](https://obsidian.md/help/callouts)—including their headers, text, and media—stay on one page whenever the callout fits within a printable page.
- Previous generated spacing can be removed and recalculated after the note changes.
- If the requested PDF does not exist yet, the skill can create the baseline export through Obsidian before beginning layout corrections.
- Spacer sizes are boundary-tested so moved content starts at the top margin instead of being pushed down by overflow from an oversized spacer.
- Adjacent whitespace-only LaTeX `align` spacers are consolidated into one dynamically sized block.

It validates layout against an actual Obsidian PDF export instead of estimating pagination from Markdown alone.
## Use

Invoke the skill as `$pdf-skill-for-obsidian` and provide the exact Markdown file or folder it may access. By default, the final PDF is saved beside the note with the same basename; an explicit PDF path overrides that convention. After temporary verification passes, the final export replaces an existing PDF at that destination. The skill does not scan unrelated vault content.

Baseline and comparison PDFs, rendered page images, and other inspection artifacts are kept outside the note folder, preferably in a run-specific directory under this repository's `tmp` directory.

Final verification first compares the exported PDF's SHA-256 with the fully inspected temporary PDF. A byte-for-byte match avoids a redundant final render; a mismatch triggers a fresh analyzer and visual review.

Example:

> Use `$pdf-skill-for-obsidian` on `Week 3.md`. Keep my current PDF margin
> and default downscale settings.

The helper requires Python 3 and uses only the standard library.
PDF layout analysis additionally uses `pdfplumber`, available in the Codex document runtime.
