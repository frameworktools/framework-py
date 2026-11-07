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

## B11. `publish_dir()` keeps the folder part of `dest` in the slug

Rule: for folder, GitHub, and Cloud destinations, `publish_dir()`'s slug is `dest` (default:
the folder's name) with each `/` turned into `-`, then normalized like every slug. Unlike
`publish_data()`, no extension is removed, since `dest` names a folder: `"reports/v1.2"`
becomes `reports-v1-2`. `publish_dir(dir, dest = "figures/meeting")` publishes as
`figures-meeting`, not `meeting`. It reports `Publishing <dest> as <slug>`, as
`publish_data()` does. Plain bucket uploads are unchanged: files go under `dest`.

R: `publish_dir()` in `R/publish.R` uses `basename(sub("/+$", "", dest))` (and
`basename(dest)` for the bundle), so R drops the folder too. Replace both with the same
`/`-to-`-` join and normalization, and add the message.

## B19. Notebook titles and acronyms: an explicit `title`

The default title rule stays as it is in both languages (`.notebook_title()` in R,
`notebook_title()` in Python):

1. A name with whitespace or any capital letter is already a title: it is kept exactly as
   typed, trimmed. `"BP for LDL and HbA1c"` stays `"BP for LDL and HbA1c"`, and
   `"BP-summary"` stays `"BP-summary"`.
2. Otherwise each run of `-` and `_` becomes one space and the first character is
   uppercased. Nothing is ever lowercased.

So every token typed in capitals already survives, which is the rule FINDINGS asked for.
The ports' problem was the input: a file-style name such as `"bp-for-ldl-and-hba1c"` has
no case information, so no rule can tell `ldl` from `and`. A list of known acronyms was
considered and rejected: it can never be complete for a field, it would need to be kept
identical in two languages, and a wrong guess ("Cat" -> "CAT") is worse than none.
Rule 1 also isn't changed to split hyphenated names with capitals (`"BP-for-LDL"` ->
`"BP for LDL"`), because that breaks names such as `"COVID-19-trends"`.

New API, the only way to fix acronyms in a lowercase name:

- `make_notebook(..., title = NULL)` and the wrappers `make_qmd()`, `make_rmd()` (R),
  `make_revealjs()`, `make_presentation()`, and `make_script()` take `title`. When given,
  it fills `{{ title }}` in the stub exactly as typed (the Jupyter stub's heading too); the
  file name still comes from `name`.

Python: keyword-only `title: str | None = None` on `make_notebook()`, `make_qmd()`,
`make_revealjs()`, `make_presentation()`, and `make_script()`.

R: add `title = NULL` after `subdir` (`make_script()`: after `overwrite`), and use
`title %||% .notebook_title(original_name)` where `make_notebook()` fills the stub,
including the `.ipynb` heading cell.

## B1. Confirming notebooks and objects that can show private data

Today the publish guard checks only file paths: a path containing `private` (any case)
is listed, a terminal session is asked `[y/N]`, and a session with no terminal fails unless
`include_private = TRUE`. Rendered notebooks and in-memory objects have no telling path, so
they published silently, even from `security: sensitive: true` projects.

Rule: the same confirmation (one warning per reason, one question, the same error without
a terminal, skipped with `include_private = TRUE`) also applies when:

1. **Sensitive project, notebook.** The settings have `security: sensitive: true` and the
   source is a `.qmd`, `.Rmd`, or `.ipynb` file, published with `publish_notebook()` or
   `publish()` (every destination: bucket, folder, GitHub, S3, Cloud).
2. **Sensitive project, object.** The settings have `security: sensitive: true` and the
   source is an object: `publish()` of a data frame, model, figure, or anything else that
   isn't a path, and `publish_data()` of a data frame. Files given by path keep the path
   rule only.
3. **Any project, notebook naming private data.** The notebook's code has a quoted string
   (single or double quotes, on one line, up to 500 characters) that either
   - has a part, split on `/`, `\`, or `.`, with no whitespace, whose name contains
     `private`, `confidential`, or `sensitive` (case-insensitive):
     `"inputs/private/raw/ids.csv"`, `"inputs.private.raw.ids"`; or
   - is a data catalog name (`a.b`, letters, digits, `_`, `-`) whose entry is private
     (in a private folder, or `private: true`), as `data_info()` reports it.

   For `.ipynb`, only code cells are read. For `.qmd`/`.Rmd`, the whole file is read
   (chunk options can name data; prose rarely quotes a path). Strings are reported in order
   of appearance, each once.

Messages (Python wording; R should match in substance):

```text
Warning: This project holds sensitive data (security: sensitive: true), and the rendered notebook 05-review.qmd can show it. Check that it holds only results that may be shared.
Warning: 05-review.qmd reads private data: inputs.private.release, outputs/private/x.csv.
Error: Not uploading this without confirmation. Review it, then pass include_private=True to upload it.
```

For an object, the first reason names its class: `this DataFrame`. When private paths are
also flagged, the error keeps saying "files with 'private' in their path".

Python: `_private.confirm_private_upload(paths, include_private, reasons)` takes the extra
reasons; `sensitive_reasons()`, `notebook_private_reads()`, and `notebook_reasons()` build
them. `publish()` checks notebooks and objects, `publish_notebook()`'s bucket and Cloud
path checks notebooks, and `publish_data()` checks data frames.

R: R's `publish()`, `publish_notebook()`, and `publish_data()` need the same three checks
in `.fw_confirm_private()` (`R/publish.R`), which should take the extra reasons, with
`security$sensitive` read from the settings.

## B2. Warning on public saves after private data was read

Nothing tracks provenance, so knowing that a particular table came from private data
isn't cheap. The rule uses the session instead:

1. `data_read()` marks the session as having read private data when the file it read is
   private: its catalog entry is private (`data_info()`'s `private`: a private folder or
   `private: true`), or, for a file path, a folder between the project root and the file
   is named like private, confidential, or sensitive (the same segment rule as the audit),
   or, for a file outside the project, `private` appears anywhere in its full path. It
   remembers the name as the caller gave it (catalog name or path), once each, in order.
2. In a project with `security: sensitive: true`, `save_table()`, `save_figure()`, and
   `save_model()` with `public = TRUE` warn after such a read, once per saved file, after
   saving and recording it:

   ```text
   Saved summary.csv as public, and this session read private data (inputs.private.raw.visits, inputs/private/raw/ids.csv, inputs.flagged, and 2 more). Check that it holds only aggregated or de-identified results before sharing it.
   ```

   At most three names are shown. Private saves, and saves in other projects, never warn.
3. The mark lasts for the session (the Python process; the R session). There is no public
   function to clear it.

Not covered: `save_report()` and `save_notebook()` (a notebook renders in its own process,
which the session can't see; B1 checks notebooks at publish time), and data reached other
than through `data_read()` (cache, database queries).

Python: `_private.note_private_read()`, `warn_public_save()`; the warning is a
`FrameworkWarning`.

R: keep the names in `.framework_env$private_reads` from `data_read()`, and warn with
`cli::cli_warn()` from `save_table()`, `save_figure()`, and `save_model()`.

## B3. Security audit and hook: duplicates, Framework's skills, publishing folders, outside folders

Rules:

1. **Each file once.** `gitignore_coverage` and `private_data_exposure` visit private
   folders deepest first (most path segments; settings order among equals) and report each
   file, and each unignored folder, once, under the deepest folder's key. R's fallback keys
   (`outputs_tables` -> `outputs/private/tables`) alias folders, which listed one staged file
   three times.
2. **Framework's own skills.** The `check_sensitive_dirs` hook skips any folder named
   `framework-*` whose parent folder is named `skills` (`.claude/skills/framework-sensitive-data`,
   `.agents/skills/...`), and doesn't descend into it, and skips `.framework/`.
3. **New check `published_outputs`** (sixth in the summary, after `secrets_exposure`).
   Candidates are `publish.destinations` entries with `driver: folder` whose `path` (relative
   to the project root unless absolute, `~` expanded) is inside the project and isn't the
   root itself. In a project with `security: sensitive: true` every such destination is
   checked; in other projects, one with `visibility: public` or `private: false` is skipped,
   since it's meant to be committed (a GitHub Pages `docs/`). A destination is a finding
   when its folder holds files that git doesn't ignore. Finding columns: `destination`,
   `path`, `files` (count not ignored), `tracked` (count of those git tracks). Status:
   `warning` when there are findings, else `pass`. Recommendation: "Add folder publishing
   destinations to .gitignore (for example /published/), or set visibility: public on a
   destination meant to be committed".
4. **Generated `.gitignore`.** Project creation appends, after the type's template and the
   `.env` rule, a `# Folder publishing destinations` comment and `/<path>/` for each
   destination rule 3 would check, unless the line is already there.
5. **Folders outside the project.** A `directories` entry whose path resolves outside the
   project root is not checked, since git can't commit it from here. The result's metadata
   gets `skipped_directories` (key -> path as configured), and the printed report adds:

   ```text
   --- Skipped: Folders Outside the Project ---
     original_private_tables: /data/other-project/outputs/private/tables
        Git can't commit them from this project, so they weren't checked.
   ```

   A path inside the project given as absolute is checked as the relative path.
6. **Watched folders.** Besides `inputs_*`, `outputs_*`, `cache`, `scratch`, and `logs`,
   the audit watches any `directories` key or path named like private, confidential, or
   sensitive (`original_private_tables`, `review/confidential`).

Python: `security.py` (`_deepest_first()`, `folder_destinations()`, `_check_published()`,
`_outside_directories()`), `hooks._framework_skill()`, `create._ignore_publish_folders()`.

R: `R/security_audit.R` (`.get_data_directories()`, `.identify_private_dirs()`, the
coverage and exposure checks, the report), the hook's sensitive-folder walk, and project
creation's `.gitignore` writer. `CHECKS` order matters for the summary table, so append
`published_outputs` after `secrets_exposure` (and D9's and D8's checks after it).

## D9. Security audit check `connection_secrets`

Rule: a new audit check, seventh in the summary, after `published_outputs`.

- Files: the settings file the audit read, and every `.yml`/`.yaml` file under the
  project's `settings/` folder (split files). Each is parsed as YAML without evaluating
  anything (`!expr` stays text).
- Scope: everything under a key named `connections`, at any depth (so `default:`, other
  environment blocks, and a split file whose top key is `connections` all count).
- A credential key is one whose name matches, case-insensitively,
  `password|passwd|pwd|secret|token|api_?key|access_key`.
- Its value is **written out** unless it is missing, empty, a mapping or list, a boolean,
  `env("VAR")` with no default, or `!expr Sys.getenv("VAR")` with no default.
  `env("VAR", "x")` and `Sys.getenv("VAR", "x")` count as written out, since the default is
  in the file. Numbers count.
- Finding columns: `file` (from the project root), `key` (dot path such as
  `connections.databases.omop.password`; list items as `[i]`), `tracked` (git tracks the
  file). Values are never stored or printed.
- Status: `fail` when any finding's file is tracked by git, else `warning` when there are
  findings (including without git), else `pass`.
- Recommendations: "Move connection passwords and tokens to .env and refer to them with
  env("VARIABLE") in the settings" and "Rotate any secret that was committed: it stays in
  git history".
- Report section "Secrets Written in Connection Settings", one line per finding:
  `settings/connections.yml: connections.databases.omop.password (tracked in git)`.

Python: `security._check_connection_secrets()`. The pre-commit hook's `data_security`
check runs it with the rest of the audit.

R: add `.check_connection_secrets()` to `R/security_audit.R` with the same rule, reading
the files with `yaml::read_yaml(eval.expr = FALSE)`.

## D8. Identifier-like columns: `data_save()` warning and audit check `identifier_columns`

Column names only, never values; files are read for their header only.

**Name rule.** Split a column name into lowercase tokens at every non-alphanumeric
character and at camelCase boundaries (`patientDOB` -> `patient`, `dob`;
`MedicalRecordNumber` -> `medical`, `record`, `number`). `joined` is the tokens run
together. The first matching kind wins, in this order:

| Kind | Matches |
|------|---------|
| `dob` | token `dob`; or token `birth` with `date`, `dt`, or `day`; or `joined` contains `dateofbirth`, `birthdate`, or `birthday` |
| `zip` | token `zip`, `zipcode`, `zip5`, `zip9`, `postal`, `postcode`, or `postalcode`; or `joined` contains `zipcode` or `postalcode` (`zip3` doesn't match) |
| `ssn` | token `ssn`; or `joined` contains `socialsecurity` |
| `mrn` | token `mrn`; or `joined` contains `medicalrecordnumber`, `medicalrecordno`, or `medrec` |
| `name` | token `name` with `first`, `last`, `given`, `family`, `full`, `middle`, `maiden`, `patient`, or `person`; or `joined` is `firstname`, `lastname`, `surname`, `fullname`, `fname`, or `lname` |

`name` columns count only when the same table has a `dob` column. Results keep column order.
Test vectors (from `tests/test_security.py::test_identifier_names`): `patientDOB`,
`date_of_birth`, `BirthDate`, `birth_dt` are dob; `birth_year`, `zip3`, `site_name`,
`zipper`, `dobutamine` match nothing; `ZIP`, `zip5`, `postal_code` are zip; `SSN` is ssn;
`mrn` and `MedicalRecordNumber` are mrn; `last_name` is name (with a dob present), and
`first_name, last_name, site` alone match nothing.

**`data_save()`.** After writing, when the file's folder (relative to the project root) has
no segment named like private, confidential, or sensitive, and the data frame's columns
match, warn (the save still happens). At most five columns are listed. In a project that
isn't `security: sensitive: true`:

```text
inputs/intermediate/patients.parquet has columns that look like direct identifiers (dob, zip_code), and it isn't in a private folder. If this is real data about people, save it in a private folder, which git ignores, and consider a privacy-sensitive project (type project_sensitive).
```

In a sensitive project the last sentence is "If this is real data about people, save it in
a private folder instead."

**Audit check `identifier_columns`** (eighth in the summary, after `connection_secrets`).
For each file git tracks (including staged files) with extension `.csv`, `.tsv`, `.txt`,
`.parquet`, `.dta`, or `.sav`, outside folders named like private, read the header (CSV:
the first line, delimiter tab if `.tsv` or a tab is present, else `;` if it has more `;`
than `,`, else `,`; Parquet: the schema; Stata/SPSS: metadata only, skipped when the reader
isn't installed). Finding columns: `file`, `columns` (matching columns, `, `-joined),
`kinds` (distinct kinds in order). Status `warning` when there are findings, never `fail`:
a header can't tell real data from synthetic. Report section "Tracked Data With
Identifier-Like Columns", one line `<file> (<columns>)`.

**Pre-commit hook.** When the `data_security` audit passes, the hook prints the findings of
`published_outputs`, `connection_secrets`, and `identifier_columns`, headed
"⚠ Security audit warnings (the commit goes ahead):", and exits 0.

Python: `framework/_identifiers.py` (`identifier_columns()`, `header_columns()`,
`warn_identifiers()`), `security._check_identifiers()`, `security.hook_warnings()`.

R: the same rule in a helper shared by `data_save()` and `R/security_audit.R`; headers via
`readLines(n = 1)`, `arrow::read_schema()` (Suggests), and `haven::read_dta(n_max = 0)`.
