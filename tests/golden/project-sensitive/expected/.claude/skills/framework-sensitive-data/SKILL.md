---
name: framework-sensitive-data
description: Use before any data operation, commit, output, or publish step in this privacy-sensitive Framework project. Covers private and public folders and the handling of identifiable data.
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
5. Never publish anything from a private folder. Never pass
   `include_private=True` to a publish function or answer its prompt on the
   user's behalf.
6. Do not read or print restricted files. Values you print are sent to the
   model provider. Work from the data dictionary and simulated data instead.

## Data flow

```
Raw identifiable data  -> inputs/private/raw/
    | clean, de-identify
Intermediate           -> inputs/private/intermediate/
    | aggregate, anonymize
Public-safe data       -> inputs/public/final/
```

## Writing code

- Do not print rows with identifiers in notebook output that will be rendered.
- Do not put identifiers, names, or record-level values in code or comments.
- Public tables follow small-cell suppression rules where they apply.
- Save outputs with `public=False` (the default) until they have been reviewed.
