"""File hashes for data integrity tracking.

The hash is the lowercase hex SHA-256 of the file's bytes, the same value R
records with ``openssl::sha256()``, so either language can verify a file the
other saved.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def file_hash(path: str | Path) -> str:
    """The SHA-256 of a file, as lowercase hex."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
