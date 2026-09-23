# Layout rules and verification

Use these rules only for Markdown/PDF paths the user explicitly selected.

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

## Complete callouts

An Obsidian callout begins with a blockquoted marker such as `> [!note]`. Treat the entire callout as one protected block: its header and all blockquoted body lines, including paragraphs, lists and their continuation lines, headings, code, tables, mathematics, images, embeds, and nested callouts. The block ends where that callout's blockquote ends.

The callout violates the rule when its header and final rendered content occur on different pages. Insert the spacer immediately before the callout header and outside the blockquote using `--outside-blockquote`. This preserves `> [!type]` as the first line of the callout. Do not insert spacing between the callout header and body, and do not alter any callout text or indentation.

If a callout is taller than one printable page at the recorded export settings, keeping it intact is impossible. Report that callout as an explicit exception instead of repeatedly adding whitespace. Do not silently fall back to protecting only its list items or paragraphs.

## Finding violations in a PDF

Render all pages to images for visual review. Also use positioned extraction (`pdftotext -bbox-layout`, `pdfplumber`, or an equivalent) to map stable text fragments to page numbers and vertical coordinates.

- Match fragments using normalized visible text; account for Markdown punctuation, inline formatting, MathJax, and callout icons being absent or transformed in extracted PDF text.
- Use distinctive words near the start and end of each target block rather than requiring an exact full-line match.
- Confirm ambiguous matches visually.
- Images or formula-only blocks need visual confirmation because text extraction may not identify them.

## Choosing spacer size

One whitespace unit is one LaTeX `\\` command. The helper encodes `N` units as `2N` literal backslash characters between `\begin{align}` and `\end{align}`.

Estimate the first size from the remaining printable height and the rendered line height. Re-export, then tune toward the smallest `N` that moves the complete protected element to the next page. Reject a change if it introduces an otherwise blank page, leaves visibly excessive whitespace, breaks the callout background, or causes a new earlier violation.

Fix and verify in document order. A change near the front can move later blocks, so never rely on stale page mappings.

## Completion evidence

The run is complete only when:

1. A fresh export made with the recorded settings was inspected page by page.
2. No H1-H6 heading is orphaned.
3. No complete callout is split across pages, except a callout proven taller than one printable page and reported to the user.
4. No accidental blank page, clipping, malformed callout, or excessive generated whitespace is visible.
5. The source diff changes only managed spacer blocks.

The official Obsidian callout syntax is documented at <https://obsidian.md/help/callouts>.
