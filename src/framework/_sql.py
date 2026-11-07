"""Splitting a SQL file into the statements ``db_execute_file()`` runs one at a time.

Statements end at a ``;`` outside quotes, comments, and PostgreSQL dollar quotes. SQLite
files are split with SQLite's own ``complete_statement()``, which also keeps a trigger's
``BEGIN ... END`` body together. SQL Server files are split into batches at lines holding
only ``GO``, as sqlcmd and SSMS do, and each batch runs whole. Statements that hold only
comments are dropped.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

_GO = re.compile(r"^[ \t]*GO[ \t]*(?:--[^\n]*)?$", re.IGNORECASE | re.MULTILINE)
_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


@dataclass(frozen=True)
class Statement:
    sql: str
    line: int  # where it starts in the file, from 1


def _is_empty(sql: str, driver: str) -> bool:
    text = _COMMENTS.sub("", sql)
    if driver == "mysql":
        text = re.sub(r"#[^\n]*", "", text)
    return not text.strip().strip(";").strip()


_LEADING = re.compile(r"(?:\s+|--[^\n]*|/\*.*?\*/)*", re.DOTALL)


def _statement(text: str, start: int, end: int) -> Statement:
    """The statement in ``text[start:end]``, with the line of its first word (after any
    leading comments), for error messages."""
    chunk = text[start:end]
    leading = _LEADING.match(chunk)
    skipped = leading.end() if leading else 0
    line = text.count("\n", 0, start + skipped) + 1
    return Statement(chunk.strip(), line)


def _split_sqlite(text: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    position = 0
    while True:
        found = text.find(";", position)
        if found == -1:
            break
        position = found + 1
        if sqlite3.complete_statement(text[start:position]):
            spans.append((start, position))
            start = position
    spans.append((start, len(text)))
    return spans


def _split_generic(text: str, driver: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        pair = text[i : i + 2]
        if pair == "--" or (char == "#" and driver == "mysql"):
            end = text.find("\n", i)
            i = length if end == -1 else end + 1
        elif pair == "/*":
            end = text.find("*/", i + 2)
            i = length if end == -1 else end + 2
        elif char in ("'", '"', "`"):
            i += 1
            while i < length:
                if driver == "mysql" and text[i] == "\\":
                    i += 2
                    continue
                if text[i] == char:
                    if text[i + 1 : i + 2] == char:  # a doubled quote is the quote itself
                        i += 2
                        continue
                    break
                i += 1
            i += 1
        elif char == "$" and driver == "postgres" and (tag := _DOLLAR.match(text, i)):
            end = text.find(tag.group(0), tag.end())
            i = length if end == -1 else end + len(tag.group(0))
        elif char == ";":
            spans.append((start, i + 1))
            start = i = i + 1
        else:
            i += 1
    spans.append((start, length))
    return spans


def split(text: str, driver: str) -> list[Statement]:
    """The statements (or SQL Server batches) in a SQL file's text."""
    text = text.removeprefix("﻿")
    if driver == "sqlserver":
        spans = []
        start = 0
        for match in _GO.finditer(text):
            spans.append((start, match.start()))
            start = match.end()
        spans.append((start, len(text)))
    elif driver == "sqlite":
        spans = _split_sqlite(text)
    else:
        spans = _split_generic(text, driver)
    statements = [_statement(text, start, end) for start, end in spans]
    return [statement for statement in statements if not _is_empty(statement.sql, driver)]
