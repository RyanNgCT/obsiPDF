# Obsidian export workflow

Use this workflow for every baseline, comparison, and final export. A missing PDF requires a baseline export; it is not a blocker.

## Export route

Use actual Obsidian PDF export. Never validate pagination with a generic Markdown-to-PDF renderer.

Use native computer control when available; otherwise, on an unlocked Windows desktop, use `scripts/export_obsidian_pdf.ps1`. Always open **Export to PDF** through the note's three-dot **More options** menu, never the command palette. Ask the user to export only if Obsidian or accessible interactive controls are unavailable after a clean retry.

If desktop access needs approval, request one reusable approval scoped to the helper and reuse it throughout the formatting run.

Record the active title, page-size, orientation, margin, and downscale settings and preserve them for every export. The reference profile is filename as title, A4 portrait, default margin, and 70% downscale; margin and downscale remain configurable.

If an export is interrupted, times out, or produces no complete non-empty PDF, rerun the identical command with the same note, output, and settings. The failed attempt supplies no layout evidence. If **Confirm Save As** appears and replacement is authorized, select **Yes**.

## Windows helper

```powershell
powershell -ExecutionPolicy Bypass -File scripts/export_obsidian_pdf.ps1 `
  -NotePath "C:\path\to\Note.md" `
  -OutputPath "C:\path\to\comparison.pdf"
```

The helper opens the exact note, uses its three-dot menu, reads and reports active settings, preserves them, saves to the exact path, handles authorized replacement, and waits for a complete non-empty PDF. Use `-Overwrite` only for run-created temporary PDFs or an authorized final replacement.

## Output lifecycle

Keep the user's named PDF unchanged during calibration. Use temporary PDFs for the clean baseline and all intermediate passes. After every pass, inspect all pages and quickly remove spacers that no longer prevent a rule violation before exporting again.

Once the temporary export and source-integrity checks pass, export to the exact named path with unchanged settings and inspect that PDF once more. Delete all temporary comparison PDFs after completion.
