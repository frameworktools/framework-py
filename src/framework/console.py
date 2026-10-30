"""Capturing printed output."""

from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field


@dataclass
class CapturedOutput:
    """Text printed inside a ``capture_output()`` block, stdout and stderr together."""

    output: str = ""
    _buffer: io.StringIO = field(default_factory=io.StringIO, repr=False)

    @property
    def lines(self) -> list[str]:
        return self.output.splitlines()


@contextmanager
def capture_output() -> Iterator[CapturedOutput]:
    """Capture everything printed to stdout and stderr inside the block.

    ::

        with fw.capture_output() as captured:
            model.summary()
        captured.lines

    Exceptions are not caught: the output printed before one is still
    available in ``captured.output``.
    """
    captured = CapturedOutput()
    try:
        with redirect_stdout(captured._buffer), redirect_stderr(captured._buffer):
            yield captured
    finally:
        captured.output = captured._buffer.getvalue()
