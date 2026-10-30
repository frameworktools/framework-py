from __future__ import annotations

from datetime import datetime


def now() -> str:
    """The current local time as an ISO 8601 string, such as ``2026-10-30T14:05:09-0400``.

    The format matches R's ``framework::now()``.
    """
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
