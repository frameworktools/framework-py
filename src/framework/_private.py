"""The check before an upload: a path with "private" in it needs confirmation.

Uploads are always the user's explicit choice, so the check never refuses
outright. Any source whose full path contains "private", in any letter case,
is listed with a warning. An interactive session asks before uploading. A
session with no terminal (scripts, CI, notebooks run by agents) raises an
error unless ``include_private=True`` is passed.
"""

from __future__ import annotations

import sys
import warnings
from collections.abc import Iterable
from pathlib import Path

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


def confirm_private_upload(paths: Iterable[Path], include_private: bool) -> None:
    """Warn about private paths among ``paths`` and ask before uploading them.

    Raises:
        FrameworkError: When there is no terminal to ask on and ``include_private`` is
            False, or when the user declines.
    """
    if include_private:
        return
    flagged = [p for p in paths if is_private_path(p)]
    if not flagged:
        return
    shown = "".join(f"\n  {p}" for p in flagged[:5])
    if len(flagged) > 5:
        shown += f"\n  ... and {len(flagged) - 5} more"
    print(
        f"Warning: {len(flagged)} file(s) to upload have 'private' in their path:{shown}",
        file=sys.stderr,
    )
    if not sys.stdin.isatty():
        raise FrameworkError(
            "Not uploading files with 'private' in their path without confirmation. "
            "Review them, then pass include_private=True to upload them."
        )
    answer = input("Upload them anyway? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise FrameworkError("Cancelled. Nothing was uploaded.")
