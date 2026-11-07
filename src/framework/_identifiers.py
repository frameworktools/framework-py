"""Columns that look like direct identifiers, found from column names alone.

A cheap check for real data about people where it doesn't belong: ``data_save()``
warns when it writes such columns outside a private folder, and the security audit
flags tracked data files whose header has them. Only names are read, never values,
so a file is opened for its header only.

A column name is split into lowercase tokens at non-alphanumeric characters and
camelCase boundaries (``patientDOB`` -> ``patient``, ``dob``), and ``joined`` is the
tokens run together. The kinds:

- dob: a token ``dob``; or tokens ``birth`` with ``date``, ``dt``, or ``day``; or
  ``joined`` containing ``dateofbirth``, ``birthdate``, or ``birthday``.
- zip: a token ``zip``, ``zipcode``, ``zip5``, ``zip9``, ``postal``, ``postcode``, or
  ``postalcode``; or ``joined`` containing ``zipcode`` or ``postalcode``. Three-digit
  ``zip3`` is not flagged.
- ssn: a token ``ssn``, or ``joined`` containing ``socialsecurity``.
- mrn: a token ``mrn``, or ``joined`` containing ``medicalrecordnumber``,
  ``medicalrecordno``, or ``medrec``.
- name: a token ``name`` with ``first``, ``last``, ``given``, ``family``, ``full``,
  ``middle``, ``maiden``, ``patient``, or ``person``; or ``joined`` equal to
  ``firstname``, ``lastname``, ``surname``, ``fullname``, ``fname``, or ``lname``.
  Names alone are common in data that isn't about people (a site's name), so they
  count only alongside a dob column.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from pathlib import Path

KINDS = ("dob", "zip", "ssn", "mrn", "name")
HEADER_TYPES = (".csv", ".tsv", ".txt", ".parquet", ".dta", ".sav")

_ZIP = {"zip", "zipcode", "zip5", "zip9", "postal", "postcode", "postalcode"}
_NAME_WITH = {"first", "last", "given", "family", "full", "middle", "maiden", "patient", "person"}
_NAMES = {"firstname", "lastname", "surname", "fullname", "fname", "lname"}


def _tokens(column: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", column)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [token for token in re.split(r"[^a-z0-9]+", spaced.lower()) if token]


def _kind(column: str) -> str | None:
    tokens = _tokens(column)
    words = set(tokens)
    joined = "".join(tokens)
    if (
        "dob" in words
        or ("birth" in words and words & {"date", "dt", "day"})
        or any(part in joined for part in ("dateofbirth", "birthdate", "birthday"))
    ):
        return "dob"
    if words & _ZIP or "zipcode" in joined or "postalcode" in joined:
        return "zip"
    if "ssn" in words or "socialsecurity" in joined:
        return "ssn"
    if "mrn" in words or any(
        p in joined for p in ("medicalrecordnumber", "medicalrecordno", "medrec")
    ):
        return "mrn"
    if ("name" in words and words & _NAME_WITH) or joined in _NAMES:
        return "name"
    return None


def identifier_columns(columns: Iterable[object]) -> list[tuple[str, str]]:
    """The columns that look like direct identifiers, with their kind, in column order.

    Name columns count only when a dob column is there too.
    """
    found = [(str(column), _kind(str(column))) for column in columns]
    has_dob = any(kind == "dob" for _, kind in found)
    return [
        (column, kind) for column, kind in found if kind is not None and (kind != "name" or has_dob)
    ]


def header_columns(path: Path) -> list[str] | None:
    """A data file's column names, reading only its header. None when the type isn't
    supported, its reader isn't installed, or the file can't be read."""
    suffix = path.suffix.lower()
    try:
        if suffix in (".csv", ".tsv", ".txt"):
            with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
                line = handle.readline(65536)
            if not line.strip():
                return None
            delimiter = (
                "\t"
                if suffix == ".tsv" or "\t" in line
                else ";"
                if line.count(";") > line.count(",")
                else ","
            )
            return next(csv.reader([line], delimiter=delimiter))
        if suffix == ".parquet":
            import pyarrow.parquet as pq

            return list(pq.read_schema(path).names)
        if suffix in (".dta", ".sav"):
            import pyreadstat

            reader = pyreadstat.read_dta if suffix == ".dta" else pyreadstat.read_sav
            _, meta = reader(str(path), metadataonly=True)
            return list(meta.column_names)
    except ImportError:
        return None
    except Exception:  # A file that can't be read is not this check's to report.
        return None
    return None


def warn_identifiers(columns: Iterable[object], target: Path, root: Path) -> None:
    """Warn when data saved outside a private folder has identifier-like columns."""
    import warnings

    from framework._paths import has_private_segment, relative_to_root
    from framework._private import project_is_sensitive
    from framework.errors import FrameworkWarning

    relative = relative_to_root(target, root)
    if has_private_segment(Path(relative).parent):
        return
    found = identifier_columns(columns)
    if not found:
        return
    shown = ", ".join(column for column, _ in found[:5])
    if len(found) > 5:
        shown += f", and {len(found) - 5} more"
    advice = (
        "If this is real data about people, save it in a private folder instead."
        if project_is_sensitive()
        else "If this is real data about people, save it in a private folder, which git "
        "ignores, and consider a privacy-sensitive project (type project_sensitive)."
    )
    warnings.warn(
        f"{relative} has columns that look like direct identifiers ({shown}), and it isn't "
        f"in a private folder. {advice}",
        FrameworkWarning,
        stacklevel=3,
    )
