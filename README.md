# PDF Skill for Obsidian

A reusable Codex skill for polishing the pagination of Obsidian-flavoured Markdown before PDF export by @RyanNgCT, co-authored by Codex.

The skill dynamically adds managed LaTeX whitespace so that:
- H1-H6 headings stay on the same page as the content they introduce.
- H2/H3 sections that would begin in the bottom tenth of a page are moved to the next page.
- Complete [Obsidian callouts](https://obsidian.md/help/callouts)—including their headers and all paragraph, list, and other body content—stay on one page whenever the callout fits within a printable page.
- Previous generated spacing can be removed and recalculated after the note changes.
- If the requested PDF does not exist yet, the skill can create the baseline export through Obsidian before beginning layout corrections.
- Spacer sizes are boundary-tested so moved content starts at the top margin instead of being pushed down by overflow from an oversized spacer.
- Adjacent whitespace-only LaTeX `align` spacers are consolidated into one dynamically sized block.

It validates layout against an actual Obsidian PDF export instead of estimating pagination from Markdown alone.
## Use

Invoke the skill as `$pdf-skill-for-obsidian` and provide the exact Markdown file or folder it may access, plus the path for the matching exported PDF. The skill does not scan unrelated vault content.

Example:
```text
Use $pdf-skill-for-obsidian on Week 3.md. Keep my current PDF margin and default downscale settings, then verify Week 3.pdf.
```

The helper requires Python 3 and uses only the standard library.
PDF layout analysis additionally uses `pdfplumber`, available in the Codex document runtime.
