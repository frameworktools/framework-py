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


def show_messages() -> None:
    """Print Framework's messages to stderr, once per process."""
    if any(getattr(handler, "_framework", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._framework = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
