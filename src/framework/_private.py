"""The check before an upload: a path with "private" in it needs confirmation.

Uploads are always the user's explicit choice, so the check never refuses
outright. Any source whose full path contains "private", in any letter case,
is listed with a warning. An interactive session asks before uploading. A
session with no terminal (scripts, CI, notebooks run by agents) raises an
error unless ``include_private=True`` is passed.

Some sources need the same confirmation for another reason, given as text: in a
project with ``security: sensitive: true``, every notebook and every in-memory
object (``sensitive_reasons()``), and in any project, a notebook whose code names
private data (``notebook_private_reads()``).
"""

from __future__ import annotations

import json
import re
import sys
import warnings
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from framework.errors import FrameworkError

# macOS resolves /tmp, /var, and /etc to /private/..., which the user did not name
_SYSTEM_PREFIX = "/private/"


def is_private_path(path: Path) -> bool:
    """Whether the full absolute path contains "private", ignoring the system prefix."""
    full = path.expanduser().resolve().as_posix()
    if full.startswith(_SYSTEM_PREFIX):
        full = full[len(_SYSTEM_PREFIX) - 1 :]
    return "private" in full.lower()


def include_private_arg(include_private: bool, allow_private: bool | None) -> bool:
    """``include_private``, honoring the deprecated ``allow_private`` name."""
    if allow_private is None:
        return include_private
    warnings.warn(
        "allow_private is deprecated; use include_private instead.",
        DeprecationWarning,
        stacklevel=3,
    )
    return include_private or allow_private


def confirm_private_upload(
    paths: Iterable[Path], include_private: bool, reasons: Sequence[str] = ()
) -> None:
    """Warn about private paths among ``paths``, and the other ``reasons``, and ask
    before uploading. One question covers them all.

    Raises:
        FrameworkError: When there is no terminal to ask on and ``include_private`` is
            False, or when the user declines.
    """
    if include_private:
        return
    flagged = [p for p in paths if is_private_path(p)]
    if not flagged and not reasons:
        return
    if flagged:
        shown = "".join(f"\n  {p}" for p in flagged[:5])
        if len(flagged) > 5:
            shown += f"\n  ... and {len(flagged) - 5} more"
        print(
            f"Warning: {len(flagged)} file(s) to upload have 'private' in their path:{shown}",
            file=sys.stderr,
        )
    for reason in reasons:
        print(f"Warning: {reason}", file=sys.stderr)
    if not sys.stdin.isatty():
        what = "files with 'private' in their path" if flagged else "this"
        raise FrameworkError(
            f"Not uploading {what} without confirmation. "
            "Review it, then pass include_private=True to upload it."
        )
    answer = input("Upload anyway? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise FrameworkError("Cancelled. Nothing was uploaded.")


def project_is_sensitive() -> bool:
    """Whether the current project's settings have ``security: sensitive: true``."""
    from framework.project import find_settings_file
    from framework.settings import settings_read

    found = find_settings_file()
    if found is None:
        return False
    try:
        security = settings_read(found).get("security")
    except FrameworkError:
        return False
    return isinstance(security, dict) and security.get("sensitive") is True


def sensitive_reasons(what: str) -> list[str]:
    """The reason to confirm publishing ``what`` (a notebook, an object) in a sensitive
    project, or none."""
    if not project_is_sensitive():
        return []
    return [
        f"This project holds sensitive data (security: sensitive: true), and {what} can "
        "show it. Check that it holds only results that may be shared."
    ]


# A quoted string in R or Python code, without f-string or raw-string prefixes
_STRING = re.compile(r""""((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)'""")
# A path or dot-name segment named like private, confidential, or sensitive
_PRIVATE_PART = re.compile(
    r"(^|[/\\.])[^/\\.\s]*(private|confidential|sensitive)[^/\\.\s]*([/\\.]|$)", re.I
)


def _notebook_code(path: Path) -> str:
    """The code of a ``.qmd``, ``.Rmd``, or ``.ipynb`` file. A Quarto file is read whole:
    prose rarely quotes a path, and chunk options may name data too."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() != ".ipynb":
        return text
    try:
        cells = json.loads(text).get("cells") or []
    except (ValueError, AttributeError):
        return ""
    code: list[str] = []
    for cell in cells:
        if isinstance(cell, dict) and cell.get("cell_type") == "code":
            source = cell.get("source", "")
            code.append("".join(source) if isinstance(source, list) else str(source))
    return "\n".join(code)


def _private_catalog_names() -> set[str]:
    """Catalog names whose files are in private folders or marked ``private: true``."""
    from framework.catalog import catalog_names, data_info

    try:
        from framework.settings import settings_read

        data: Any = settings_read().get("data")
        names = catalog_names(data) if data else []
    except FrameworkError:
        return set()
    private = set()
    for name in names:
        try:
            if data_info(name).private:
                private.add(name)
        except FrameworkError:
            continue
    return private


def notebook_private_reads(path: Path) -> list[str]:
    """The strings in a notebook's code that name private data, in order, once each.

    A string counts when a ``/``- or ``.``-separated part of it is named like private,
    confidential, or sensitive (``"inputs/private/raw/ids.csv"``,
    ``"inputs.private.raw.ids"``), or when it is a data catalog name whose file is private.
    This is a quick text check, not an analysis: data reached any other way is not found.
    """
    try:
        code = _notebook_code(path)
    except OSError:
        return []
    strings = [a or b for a, b in _STRING.findall(code)]
    catalog: set[str] | None = None
    found: list[str] = []
    for value in dict.fromkeys(strings):
        if not value or len(value) > 500 or value in found:
            continue
        if _PRIVATE_PART.search(value):
            found.append(value)
            continue
        if re.fullmatch(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_-]+)+", value):
            if catalog is None:
                catalog = _private_catalog_names()
            if value in catalog:
                found.append(value)
    return found


def notebook_reasons(path: Path) -> list[str]:
    """Why publishing the notebook at ``path`` needs confirmation, or none."""
    reasons = sensitive_reasons(f"the rendered notebook {path.name}")
    reads = notebook_private_reads(path)
    if reads:
        shown = ", ".join(reads[:3]) + (f", and {len(reads) - 3} more" if len(reads) > 3 else "")
        reasons.append(f"{path.name} reads private data: {shown}.")
    return reasons
