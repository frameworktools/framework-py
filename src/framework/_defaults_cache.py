"""The cached Framework Cloud defaults, for creating projects offline.

After each successful resolve of your defaults, ``new()`` saves the envelope
here, one file per server and language, in the format R and ``fw`` share:

    <config folder>/cache/defaults/<host[_port]>/<language>.json
    {"fetched_at": "<RFC 3339>", "defaults_revision": "...", "envelope": {...}}

Each ":" in host[:port] becomes "_", because Windows doesn't allow ":" in
folder names.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from framework.config_home import config_dir


def cache_path(url: str, language: str) -> Path:
    from framework.cloud import host

    return config_dir() / "cache" / "defaults" / host(url).replace(":", "_") / f"{language}.json"


def write_cached_defaults(
    url: str, language: str, envelope: dict[str, Any], revision: str | None
) -> None:
    path = cache_path(url, language)
    path.parent.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    record = {"fetched_at": fetched_at, "defaults_revision": revision, "envelope": envelope}
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def read_cached_defaults(url: str, language: str) -> dict[str, Any] | None:
    """The cached record, or None when there is none or it can't be read."""
    try:
        record = json.loads(cache_path(url, language).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("envelope"), dict):
        return None
    return record
