---
name: framework-sensitive-data
description: Use before any data operation, commit, output, or publish step in this privacy-sensitive Framework project. Covers private and public folders and the handling of identifiable data.
metadata:
  title: Handling sensitive data
  summary: Keep private data in private folders, out of Git and published work.
---

# Sensitive Data

This project holds identifiable or restricted data. When it is unclear
whether something is safe, treat it as private.

## Rules

1. Files in `inputs/private/` and `outputs/private/` are never committed. They
   can identify people, and the `.gitignore` keeps them out of git.
2. Raw data with identifiers goes in a `private/` folder.
3. Only de-identified, aggregated data goes in a `public/` folder.
4. Review every output before saving it to a public folder.
5. Save sensitive data into a private folder:
   `fw.data_save(df, "inputs.private.intermediate.cleaned")`, or a path under
   `inputs/private/` or `outputs/private/`.
6. Run `fw.git_security_audit()` before commits to scan for data leaks, and
   keep the `data_security` git hook on (`fw.git_hooks_list()`,
   `fw.git_hooks_enable("data_security")`).
7. Never publish anything from a private folder. Never pass
   `include_private=True` to a publish function or answer its prompt on the
   user's behalf.
8. Do not read or print restricted files. Values you print are sent to the
   model provider. Work from the data dictionary and simulated data instead.

## Data flow

```
Raw identifiable data  -> inputs/private/raw/
    | clean, de-identify
Intermediate           -> inputs/private/intermediate/
    | aggregate, anonymize
Public-safe data       -> inputs/public/final/
```

## Learning a file's layout

To learn what a private or restricted file holds, use
`fw.data_info(name, columns=True)` instead of reading the file, printing it, or
calling `.head()`, `.info()`, or `.describe()` on it:

```python
info = fw.data_info("inputs.private.raw.patients", columns=True)
info.columns  # name, type, and count of missing values for each column
info.rows  # number of rows
```

It never shows values, category levels, ranges, or examples, and it records
nothing in framework.db.

## Writing code

- Do not print rows with identifiers in notebook output that will be rendered.
- Do not put identifiers, names, or record-level values in code or comments.
- Public tables follow small-cell suppression rules where they apply.
- Save outputs with `public=False` (the default) until they have been reviewed.
