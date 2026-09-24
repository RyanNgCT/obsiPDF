# Layout rules and verification

Use these rules only for Markdown/PDF paths the user explicitly selected.

All user-authored Markdown is immutable during pagination work. No heading or text may be deleted, renamed, reordered, or rewritten. Only whitespace-only LaTeX `align` spacer blocks may change. Verify the final source against a pre-edit content inventory that excludes only those spacer blocks and obsolete formatter marker comments.

## Export profile

Pagination depends on the entire Obsidian rendering environment: page size and orientation, margins, downscale, title inclusion, theme, CSS snippets, fonts, images, callout styling, and Obsidian version. Record the active choices at the start of a run and keep them fixed until final verification.

The reference profile is:

- Include file name as title: on
- Page size: A4
- Orientation: portrait
- Margin: Default
- Downscale percent: 70

Margin and downscale are intentionally configurable. A result is valid only for the settings used to export and verify it.

## What counts as following content

For an H1-H6 heading, the first substantive block is the next rendered paragraph, list item, callout, table, code block, image/embed, or mathematical block. Blank lines, HTML comments, and managed spacers do not count. If the next Markdown heading appears before substantive content, keep the heading group together with the first substantive block after the group.

A heading is orphaned when its rendered text is on page `p` and that first substantive block begins on a later page. Insert spacing before the earliest heading in the group, not between grouped headings.

In addition, an H2 or H3 is too close to the page boundary when the top of its rendered heading is in the bottommost tenth of the physical PDF page (`top / page_height >= 0.90`). Move that section heading to the next page even if the first following block still fits on the original page. Measure against the full PDF page height, not an eyeballed text-area fraction. `analyze_layout.py` reports this as `heading-bottom-tenth`; confirm the result visually.

## Complete callouts

An Obsidian callout begins with a blockquoted marker such as `> [!note]`. Treat the entire callout as one protected block: its header and all blockquoted body lines, including paragraphs, lists and their continuation lines, headings, code, tables, mathematics, images, embeds, and nested callouts. The block ends where that callout's blockquote ends.

The callout violates the rule when its header and final rendered content occur on different pages. Insert the spacer immediately before the callout header and outside the blockquote using `--outside-blockquote`. This preserves `> [!type]` as the first line of the callout. Do not insert spacing between the callout header and body, and do not alter any callout text or indentation.

If a callout is taller than one printable page at the recorded export settings, keeping it intact is impossible. Report that callout as an explicit exception instead of repeatedly adding whitespace. Do not silently fall back to protecting only its list items or paragraphs.

## Finding violations in a PDF

Render all pages to images for visual review. Also use positioned extraction (`pdftotext -bbox-layout`, `pdfplumber`, or an equivalent) to map stable text fragments to page numbers and vertical coordinates.

Start with `scripts/analyze_layout.py <note.md> <export.pdf> --pretty`. It identifies likely page mismatches for headings and complete callouts. Confirm every reported violation visually, and visually inspect every unresolved block; text extraction is supporting evidence rather than the final authority.

- Match fragments using normalized visible text; account for Markdown punctuation, inline formatting, MathJax, and callout icons being absent or transformed in extracted PDF text.
- Use distinctive words near the start and end of each target block rather than requiring an exact full-line match.
- Confirm ambiguous matches visually.
- Images or formula-only blocks need visual confirmation because text extraction may not identify them.

## Choosing spacer size

One whitespace unit is one LaTeX `\\` command. The helper encodes `N` units as `2N` literal backslash characters between `\begin{align}` and `\end{align}`.

Treat spacer sizing as an integer boundary search, not a visual padding choice:

Record each attempt's target type, source anchor, page and vertical coordinate, spacer unit count, crossing result, destination top coordinate, and whether the result underfilled or overcompensated. Keep the latest non-overcompensated count as a lower bound and the first overcompensated count as an upper bound. Once a comparable attempt exists, derive the next size from that evidence rather than starting from a generic guess.

1. Start with a small `N` or a conservative height-based estimate.
2. When the target is far from the required boundary, choose the next change from measured remaining height and earlier unit-to-height behavior. A jump of 6–10 units is reasonable when the evidence supports it, but it is not a fixed minimum or maximum.
3. As soon as a trial brackets the boundary, refine by one or two units. Use the smaller step when the target is very close or when the previous change overcompensated.
4. If a heading remains on the preceding page while its following callout or substantive content has moved, classify the trial as underfilled and keep increasing the spacer until both cross together. Overcompensation begins only after the complete protected target has crossed and generated whitespace appears above it, it starts materially below the normal top position, or an accidental blank page appears. Then immediately restore the recorded lower-bound count and test only one or two units above it.
5. Use `manage_spacers.py set-legacy-before` to resize the block and keep the smallest count that satisfies the protected-block invariant without spacer overflow.
6. When an earlier correction repaginates a downstream target so it fits without generated whitespace, use `manage_spacers.py remove-legacy-before` and re-export. Zero generated units is preferable to retaining a stale spacer.

Adjacent compatible whitespace-only `align` blocks are one logical spacer and must be physically combined into one block. Add their `\\` unit counts, then use `manage_spacers.py merge-adjacent` or `set-legacy-before` to canonicalize them. Do not preserve separate nudge blocks and do not use HTML breaks as formatter controls.

Every pre-existing HTML `<br>` line is user-owned and intentional. Preserve it byte-for-byte. Its rendered height remains part of the baseline pagination, but the formatter must never add, remove, move, resize, or normalize it.

The destination heading or callout must start at Obsidian's normal top content position, within roughly one rendered line of the printable top margin. The generated spacer must end on the preceding page at, or within one rendered line of, the printable bottom margin. If blank generated space is visible at the top of the destination page, the spacer overflowed and is too large even if the block no longer splits.

Reject a change if it introduces an otherwise blank page, leaves excessive whitespace on either side of the page boundary, breaks the callout background, or causes a new earlier violation.

Fix and verify in document order. A change near the front can move later blocks, so never rely on stale page mappings. The agent should still complete the full selected note in one run: repeat analysis and export until a complete pass from the first page through the final page is clean. Batch corrections only when their page ranges cannot influence one another; otherwise fix the earliest violation first.

## Completion evidence

The run is complete only when:

1. A fresh export made with the recorded settings was inspected page by page.
2. No H1-H6 heading is orphaned.
3. No H2 or H3 begins in the bottom tenth of a page.
4. No complete callout is split across pages, except a callout proven taller than one printable page and reported to the user.
5. No adjacent compatible whitespace-only `align` blocks remain separate.
6. No accidental blank page, clipping, malformed callout, or excessive generated whitespace is visible.
7. No `obsidian-pdf-formatter` marker annotation is visible or remains in the Markdown at any calibration stage.
8. The source diff changes only annotation-free LaTeX spacer blocks; all pre-existing `<br>` lines are unchanged.
9. Every original heading and all original user text remain present byte-for-byte and in the same order.

The official Obsidian callout syntax is documented at <https://obsidian.md/help/callouts>.
