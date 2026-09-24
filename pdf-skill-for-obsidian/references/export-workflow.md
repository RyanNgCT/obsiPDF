# Obsidian export workflow

Use this workflow when the requested PDF is absent or when a changed Markdown file needs a fresh comparison export.

## Preferred routes

1. Use a native computer-control facility when it can operate the installed Obsidian application and read the export dialog.
2. On Windows with an interactive desktop, run `scripts/export_obsidian_pdf.ps1`. The script uses Obsidian's URI handler and Windows UI Automation; it does not imitate Obsidian with a different Markdown renderer.
3. Ask the user for an export only if neither route is available, the desktop is locked, Obsidian is not installed, or accessibility controls cannot be reached.

Do not substitute a generic Markdown-to-PDF tool for layout validation. Its callout, MathJax, theme, CSS, image, font, and pagination behavior can differ from Obsidian.

If desktop automation requires elevated access, request approval once with a reusable command prefix limited to `export_obsidian_pdf.ps1`. That one approval covers every export required by the current formatting run. Do not issue a new permission request for each iteration.

If the run is interrupted, the command times out, or no complete output PDF appears, rerun the identical helper command. Preserve the same note path, output path, and export settings, and reuse the existing approval. An interrupted attempt provides no pagination evidence. Request manual export only when a clean retry also fails because Obsidian, its interactive desktop, or its accessible controls are unavailable.

When `-Overwrite` is supplied and Windows shows **Confirm Save As**, choose **Yes** to replace the existing comparison or final PDF. The helper waits for and accepts this prompt automatically.

## Windows helper

```powershell
powershell -ExecutionPolicy Bypass -File scripts/export_obsidian_pdf.ps1 `
  -NotePath "C:\path\to\Note.md" `
  -OutputPath "C:\path\to\Note.pdf"
```

The helper:

- opens the exact note through `obsidian://open?path=...`;
- invokes `Export to PDF...` from the note's three-dot **More options** menu;
- never invokes the command palette; if the three-dot note menu is temporarily unavailable, rerun the same menu workflow;
- reads and reports title inclusion, page size, orientation, margin, and downscale;
- preserves those settings rather than assigning defaults;
- saves to the exact output path; and
- waits for a completed, non-empty PDF.

After each helper run, render and inspect the resulting PDF automatically. Reuse the approved helper command when another export is required; PDF inspection itself is read-only and must not trigger another export permission request.

Use `-Overwrite` only for a comparison file created during the current run or when the user authorized replacing the named PDF. The helper emits a JSON record of the settings and output path; retain that record for all comparison exports.

The helper requires an unlocked interactive Windows desktop and an Obsidian build exposing accessible controls. If it fails, report the concrete failing stage before requesting a manual export.

## Iteration outputs

Keep the user's named PDF stable during experimentation. Export baseline and intermediate versions to a clearly named temporary PDF, inspect them, and delete them when the run finishes. Export the final source to the requested path with the same settings, then inspect that exact final PDF once more.
