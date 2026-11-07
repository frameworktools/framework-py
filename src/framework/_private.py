"""The check before an upload: a file in a private folder needs confirmation.

Uploads are always the user's explicit choice, so the check never refuses
outright. Any source in a private folder (``is_private_path()``) is listed
with a warning. An interactive session asks before uploading. A
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

from framework._paths import in_root
from framework.errors import FrameworkError, FrameworkWarning

# macOS resolves /tmp, /var, and /etc to /private/..., which the user did not name
_SYSTEM_PREFIX = "/private/"


def is_private_folder_name(name: str) -> bool:
    """Whether a folder name marks it private: ``private`` or ``..._private``, in any case."""
    lowered = name.lower()
    return lowered == "private" or lowered.endswith("_private")


def private_directories(root: Path | None = None) -> list[Path]:
    """The folders the settings' ``directories`` mark private by key
    (``outputs_private_tables``), resolved. Keys that also say public are left out."""
    from framework.project import find_settings_file
    from framework.settings import settings_read

    found = find_settings_file(root)
    if found is None:
        return []
    root = found.parent
    try:
        directories = settings_read(found).get("directories")
    except FrameworkError:
        return []
    if not isinstance(directories, dict):
        return []
    return [
        in_root(value, root).resolve()
        for key, value in directories.items()
        if isinstance(value, str)
        and value
        and "private" in str(key).lower()
        and "public" not in str(key).lower()
    ]


def is_private_path(path: Path, private_dirs: Sequence[Path] | None = None) -> bool:
    """Whether ``path`` is in a private folder.

    A path is private when one of its folders (not the file name) is named ``private`` or
    ends in ``_private``, in any letter case, or when it is under a folder the settings
    mark private (``private_directories()``). macOS's ``/private/`` system prefix does not
    count. A folder's own name counts when ``path`` is a folder.
    """
    resolved = path.expanduser().resolve()
    full = resolved.as_posix()
    if full.startswith(_SYSTEM_PREFIX):
        full = full[len(_SYSTEM_PREFIX) - 1 :]
    parts = full.split("/")
    folders = parts if resolved.is_dir() else parts[:-1]
    if any(is_private_folder_name(part) for part in folders):
        return True
    dirs = private_directories() if private_dirs is None else private_dirs
    return any(resolved == folder or folder in resolved.parents for folder in dirs)


def is_private_data(path: Path, root: Path | None) -> bool:
    """Whether a data file is private: a folder below the project root named like private,
    confidential, or sensitive, or, for a file outside the project, "private" anywhere in
    its full path (as the publish check reads paths)."""
    from framework._paths import has_private_segment

    full = path.expanduser().resolve()
    if root is not None:
        try:
            return has_private_segment(full.parent.relative_to(root.resolve()))
        except ValueError:
            pass
    return is_private_path(full)


# Private data read in this session ------------------------------------------------------

_private_reads: list[str] = []


def note_private_read(name: str) -> None:
    """Remember that this session read private data, named as the caller gave it."""
    if name not in _private_reads:
        _private_reads.append(name)


def private_reads() -> list[str]:
    """The private data this session has read, in order."""
    return list(_private_reads)


def forget_private_reads() -> None:
    _private_reads.clear()


def private_read_note() -> str:
    """In a sensitive project that read private data this session, the names read, as
    "inputs.x, inputs/y.csv, and 2 more"; otherwise ""."""
    if not _private_reads or not project_is_sensitive():
        return ""
    shown = ", ".join(_private_reads[:3])
    if len(_private_reads) > 3:
        shown += f", and {len(_private_reads) - 3} more"
    return shown


def warn_public_save(path: Path, public: bool) -> None:
    """Warn when a public output is saved in a sensitive project after private data was
    read in this session: the output may have been made from it."""
    shown = private_read_note() if public else ""
    if not shown:
        return
    warnings.warn(
        f"Saved {path.name} as public, and this session read private data ({shown}). "
        "Check that it holds only aggregated or de-identified results before sharing it.",
        FrameworkWarning,
        stacklevel=3,
    )


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
    dirs = private_directories()
    flagged = [p for p in paths if is_private_path(p, dirs)]
    if not flagged and not reasons:
        return
    if flagged:
        shown = "".join(f"\n  {p}" for p in flagged[:5])
        if len(flagged) > 5:
            shown += f"\n  ... and {len(flagged) - 5} more"
        print(
            f"Warning: {len(flagged)} file(s) to upload are in private folders:{shown}",
            file=sys.stderr,
        )
    for reason in reasons:
        print(f"Warning: {reason}", file=sys.stderr)
    if not sys.stdin.isatty():
        what = "files from private folders" if flagged else "this"
        raise FrameworkError(
            f"Not uploading {what} without confirmation. "
            "Review it, then pass include_private=True to upload it."
        )
    # As R asks: "them" when only paths are flagged
    question = "Upload anyway?" if reasons else "Upload them anyway?"
    answer = input(f"{question} [y/N] ").strip().lower()
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
# A catalog name: dot-separated words
_CATALOG_NAME = re.compile(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_-]+)+")


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


def _private_path_string(value: str, root: Path | None, dirs: Sequence[Path]) -> bool:
    """Whether a string is a path with a private folder part, or under a private folder
    from the settings. The last part counts as a folder only when it has no extension, so
    a file named ``01-private-notes.qmd`` doesn't count."""
    if "/" not in value and "\\" not in value:
        return False
    parts = re.split(r"[/\\]", value)
    last = parts[-1]
    folders = parts[:-1] if "." in last else parts
    if any(is_private_folder_name(part) for part in folders):
        return True
    if not dirs or root is None:
        return False
    try:
        candidate = in_root(value, root).resolve()
    except (OSError, ValueError):
        return False
    return any(candidate == folder or folder in candidate.parents for folder in dirs)


def notebook_private_reads(path: Path) -> list[str]:
    """The strings in a notebook's code that name private data, in order, once each.

    A string counts when it is a path with a private folder part
    (``"inputs/private/raw/ids.csv"``, ``"outputs_private/x.csv"``) or under a folder the
    settings mark private, or when it is a data catalog name whose file is private. Other
    strings, such as settings keys or ``"private|public"``, never count. This is a quick
    text check, not an analysis: data reached any other way is not found.
    """
    from framework.project import find_settings_file

    found_settings = find_settings_file()
    root = found_settings.parent if found_settings is not None else None
    dirs = private_directories(root) if root is not None else []
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
        if _private_path_string(value, root, dirs):
            found.append(value)
            continue
        if _CATALOG_NAME.fullmatch(value):
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
