"""Framework's progress messages.

Messages go to the ``framework`` logger. A library stays quiet unless the
caller configures logging. ``scaffold()`` and project creation call
``show_messages()`` so interactive work shows them, as R's messages do.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("framework")
logger.addHandler(logging.NullHandler())


def inform(message: str) -> None:
    logger.info(message)


class _CurrentStderr(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever ``sys.stderr`` is now, so ``capture_output()`` sees messages."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stderr
        super().emit(record)


def show_messages() -> None:
    """Print Framework's messages to stderr, once per process."""
    if any(getattr(handler, "_framework", False) for handler in logger.handlers):
        return
    handler = _CurrentStderr(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._framework = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
