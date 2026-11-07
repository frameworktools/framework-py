# Parity proposals: outputs, publishing, and security

framework-py added or changed these APIs after porting real R projects to Python
(`framework-ports/FINDINGS.md`, 2026-11-07). The R package should mirror each one after
CRAN accepts 2.0.0. Each section gives the rule both languages follow, the Python
implementation, and what R needs to change. Item codes (B9, D14, ...) are the ones in
`FINDINGS.md`.

## B9. `save_figure()` and `save_table()`: sizes, the caller's figure, and subfolders

Rule:

1. A `name` with `/` names subfolders of the output folder. They are created, as the
   output folder itself is. `save_table(df, "meeting-2026-10-16/cohort")` writes
   `outputs/tables/meeting-2026-10-16/cohort.csv`; the results row is keyed by that path.
2. `width` and `height` (defaults 8 and 6 inches, unchanged) set the size of the saved
   file for every kind of plot the language accepts, including multi-panel compositions.
3. Saving never changes the object passed in. A figure that has to be resized to save it
   is put back at its own size afterwards.

Python: `save_figure()` restores a matplotlib figure's size after `savefig()`. A plotnine
composition (`plotnine.composition.Compose`, detected by class name so plotnine is never
imported for other plots) is sized with `plot & theme(figure_size=(width, height))`, which
copies it, then drawn and saved, because `Compose.save()` ignores `width` and `height`.

R: `ggsave()` already sizes patchwork compositions and doesn't touch the plot, so only rule
1 needs work: create `dirname(file_path)` with `dir.create(recursive = TRUE)` in
`save_figure()`, `save_table()`, and `save_model()` before writing.

## D14. `save_figure()` saves several formats, and eps and emf

Rule:

1. `format` accepts one format or a vector/list of them. Each format writes
   `<name>.<format>` and gets its own results row. The return value is the path for a
   single format (a string in Python, a length-1 character vector in R) and the paths in
   the given order for several. Duplicates are dropped, keeping the first.
2. Allowed formats: `png`, `pdf`, `svg`, `eps`, `jpg`, `jpeg`, `tiff`, `emf`.
3. Every format, and every existing file when `overwrite` is false, is checked before any
   file is written, so a failed call writes nothing.
4. `emf` is written when the language has a device for it, and otherwise is an error that
   says to save svg or pdf and convert.

Python: `format: str | list[str] | tuple[str, ...]`, typed with overloads (`Path` for a
string, `list[Path]` for a list). eps comes from matplotlib's PostScript backend. Python
raises `UnsupportedFormatError` for emf unless a matplotlib backend is registered for it,
which none is today.

R: `checkmate::assert_subset(format, c(..., "eps", "emf"))`, then loop. eps through
`ggsave(device = grDevices::cairo_ps)` or `"eps"`, and for recorded plots
`grDevices::postscript(onefile = FALSE, horizontal = FALSE, paper = "special")`. emf
through `devEMF::emf` when installed (a Suggests), else the same error as Python.

## B10. `publish_data()` reports the slug the artifact gets

Rule: the "Publishing <dest> as <slug>" message shows the final slug, after the same
normalization every slug gets (`artifact_slug()`: lowercase, each run of characters other
than `a-z` and `0-9` becomes `-`, leading and trailing `-` removed).
`publish_data(df, "tables/bp_by_visit.csv")` says `as tables-bp-by-visit`, not
`tables-bp_by_visit`.

Python: `_data_slug()` returns the normalized slug, so the message, the folder, and the
manifest agree.

R: normalize in R's `publish_data()` slug helper before the message (`.artifact_slug()` or
its equivalent), and pass the normalized slug on.
