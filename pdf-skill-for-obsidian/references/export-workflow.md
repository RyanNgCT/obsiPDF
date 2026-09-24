# Obsidian export workflow

Use this workflow for every baseline, comparison, and final export. A missing PDF requires a baseline export; it is not a blocker.

## Output paths

Unless the user explicitly specifies another destination, the final PDF belongs beside the selected note and uses the Markdown file's exact basename with a `.pdf` extension. For example, `C:\vault\Week 3.md` produces `C:\vault\Week 3.pdf`. This convention is the default and does not require a separate confirmation question.

Never place baseline PDFs, comparison PDFs, page-render images, screenshots, or other inspection artifacts in the selected note's folder. Create a run-specific directory outside the vault folder, preferably beneath the `tmp` directory at the repository root containing this skill, and keep all temporary artifacts there.

## Export route

Use actual Obsidian PDF export. Never validate pagination with a generic Markdown-to-PDF renderer.

Use native computer control when available; otherwise, on an unlocked Windows desktop, use `scripts/export_obsidian_pdf.ps1`. Always open **Export to PDF** through the note's three-dot **More options** menu, never the command palette. Ask the user to export only if Obsidian or accessible interactive controls are unavailable after a clean retry.

If desktop access needs approval, request one reusable approval scoped to the helper and reuse it throughout the formatting run.

Record the active title, page-size, orientation, margin, and downscale settings and preserve them for every export. The reference profile is filename as title, A4 portrait, default margin, and 70% downscale; margin and downscale remain configurable.

If an export is interrupted, times out, or produces no complete non-empty PDF, rerun the identical command with the same note, output, and settings. The failed attempt supplies no layout evidence. The verified final export replaces an existing PDF at its destination; when **Confirm Save As** appears, select **Yes**. Do not replace the final during baseline or calibration exports.

## Windows helper

```powershell
powershell -ExecutionPolicy Bypass -File scripts/export_obsidian_pdf.ps1 `
  -NotePath "C:\path\to\Note.md" `
  -OutputPath "C:\path\to\obsiPDF\tmp\run-id\comparison.pdf"
```

For the final export, derive the same-directory, same-basename `.pdf` path from `-NotePath`, pass it explicitly as `-OutputPath`, and add `-Overwrite` when that final already exists. The helper opens the exact note, uses its three-dot menu, reads and reports active settings, preserves them, saves to the exact path, handles replacement, and waits for a complete non-empty PDF. Use `-Overwrite` only for run-created temporary PDFs or the verified final replacement.

## Output lifecycle

Keep the final PDF unchanged during calibration. Use the run-specific directory outside the note folder for the clean baseline, all intermediate PDFs, and all rendered inspection images. After every pass, inspect all pages and quickly remove spacers that no longer prevent a rule violation before exporting again.

Once the temporary export and source-integrity checks pass, record that verified PDF's SHA-256 and export to the confirmed or derived final path with unchanged settings. Compare the two PDF files with a cryptographic hash, for example `Get-FileHash -Algorithm SHA256` on Windows. If the hashes match, treat the final as byte-for-byte identical to the already inspected temporary PDF and do not rerender or reinspect it. If they differ, retain the temporary reference and run the complete analyzer, render, and visual-inspection workflow on the final PDF. Delete all temporary PDFs and images only after one of these verification routes passes.
