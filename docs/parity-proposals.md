# Parity proposals for the R package

framework-py changes that the R package should mirror after CRAN accepts 2.0.0. Each entry
gives the R signature, the behavior both languages share, and what R does today. Names,
arguments, and semantics are meant to be identical; where a language must differ, the
entry says so.

The items come from porting real R projects to Python (FINDINGS.md in framework-ports,
item numbers in brackets).

## data_read(): readr's missing values and whitespace in CSV and TSV files [B4]

```r
data_read(path, delim = NULL, keep_attributes = FALSE, ...)
```

Shared behavior for `csv` and `tsv` files:

- Only `""` and `"NA"` are missing. `na =` replaces the list.
- Leading and trailing whitespace is trimmed from values and column names
  (`trim_ws = TRUE`). A value that is a missing-value string after trimming is missing,
  and a column whose values are all numbers after trimming is numeric.
- `trim_ws = FALSE` keeps the whitespace.

Python: `data_read(path, delim=None, keep_attributes=False, *, na=None, trim_ws=None,
**kwargs)`. `na` takes a string or a list. pandas' own `na_values` and `keep_default_na`
are used as given when passed, and passing both `na` and `na_values` is an error. Columns
typed with pandas' `dtype=` are trimmed but not retyped; `converters=` columns are left
alone. `na` and `trim_ws` on a format other than CSV, TSV, or Excel are an error.

R today: `readr::read_delim()` already reads only `""` and `"NA"` as missing, but its
`trim_ws` default is FALSE (readr 2.2.0; `read_csv()` defaults to TRUE, `read_delim()`
does not). So R keeps `"  hi "` and reads `"    1"` as text.

R change: pass `trim_ws = TRUE` to `read_delim()` for `csv` and `tsv` unless the caller
gives `trim_ws` in `...`. This changes what R returns for padded files; mention it in
NEWS.

## data_read(): R's reader argument names, and readxl's rules for Excel [B5]

R needs no change: its `data_read()` passes `...` to `readr::read_delim()` and
`readxl::read_excel()`, so these names already work there. Python takes the same names as
keyword-only arguments and translates them to pandas, so a call copied from R reads the
same data:

| R name | Applies to | Python behavior |
|--------|------------|-----------------|
| `sheet` | Excel | Name, or position counting from 1 (pandas counts from 0) |
| `col_names` | CSV, TSV, Excel | `TRUE`/`FALSE`/names. `FALSE` names columns `X1, X2, ...` (readr) or `...1, ...2, ...` (readxl) |
| `col_types` | CSV, TSV | readr's compact string (`"cid_"`) or a named mapping of one-letter codes |
| `col_types` | Excel | readxl's types (`"guess"`, `"text"`, `"numeric"`, `"logical"`, `"date"`, `"list"`, `"skip"`), one or one per column |
| `skip` | CSV, TSV, Excel | Rows skipped before the names |
| `n_max` | CSV, TSV, Excel | Most data rows read |
| `na` | CSV, TSV, Excel | Missing-value strings. Excel default: `""` only (readxl) |
| `trim_ws` | CSV, TSV, Excel | Trim whitespace. Default TRUE (readxl's default) |

Shared Excel rules (readxl's defaults): only empty cells are missing, so the text `"NA"`
is text; text cells are trimmed. Python reads readr's `cols()` objects as a mapping of
names to codes; R's `cols()` specification objects have no Python form beyond that.

Known differences that remain: readxl gives a column that mixes numbers and text as text
(`"4"`), while pandas keeps the mixed values; readxl reads an Excel time-only cell as a
date-time on 1899-12-31, pandas as a `datetime.time`; readr's `n` parses numbers with
grouping marks (`"1,000"`), Python's `n` does not.
