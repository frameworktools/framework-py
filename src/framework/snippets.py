"""Code snippets: a cell's or a block's code and the text it printed.

A code snippet is a ``framework.artifact/v1`` document with ``kind: "code_snippet"``.
Framework Cloud highlights the code, styles ANSI output, and applies the size limits,
so this module only records what ran and what it printed.

In Jupyter, ``%load_ext framework`` adds the ``%%fw_snippet`` cell magic. In a script,
``with fw.snippet("label"):`` records the block.
"""

from __future__ import annotations

import ast
import json
import linecache
import os
import re
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

from framework._log import inform
from framework.errors import FrameworkError

SCHEMA = "framework.artifact/v1"
KIND = "code_snippet"
LINK_RELS = ("notebook", "model")
_OPTION_LINE = re.compile(r"^\s*#\|")
_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_NOTEBOOK_SUFFIXES = (".ipynb", ".qmd", ".md")


# ---------------------------------------------------------------------------
# The document


def _links(links: dict[str, str] | None) -> list[dict[str, str]]:
    """``display.links`` from ``{"notebook": "https://...", "model": "slug or URL"}``."""
    if not links:
        return []
    if not isinstance(links, dict):
        raise FrameworkError(
            'links must be a dict such as {"notebook": "https://...", "model": "model-slug"}.'
        )
    out: list[dict[str, str]] = []
    for rel, value in links.items():
        if rel not in LINK_RELS:
            raise FrameworkError(
                'links must be named "notebook" or "model", as in '
                '{"notebook": "https://...", "model": "model-slug"}.'
            )
        if not isinstance(value, str) or not value:
            raise FrameworkError("Each link must be one string.")
        if re.fullmatch(r"https://\S+", value):
            out.append({"rel": rel, "url": value})
        elif rel == "model" and _SLUG.fullmatch(value):
            out.append({"rel": rel, "artifact": value})
        elif rel == "model":
            raise FrameworkError(
                "A model link takes an artifact slug in this project or an https URL."
            )
        else:
            raise FrameworkError("A notebook link takes an https URL.")
    return out


def _document(
    blocks: Sequence[dict[str, Any]],
    source: dict[str, Any],
    *,
    output: bool = True,
    folded: bool | None = None,
    links: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """The snippet document, or None when it has no code.

    Adjacent output blocks join, as a notebook shows them. With ``output=False`` only
    the code is kept.
    """
    kept = [dict(block) for block in blocks if block.get("text")]
    if not output:
        kept = [block for block in kept if block["type"] == "code"]
    merged: list[dict[str, Any]] = []
    for block in kept:
        if merged and merged[-1]["type"] == block["type"] and block["type"] in ("code", "output"):
            merged[-1]["text"] = merged[-1]["text"] + "\n" + block["text"]
        else:
            merged.append(block)
    if not any(block["type"] == "code" for block in merged):
        return None
    if folded is not None:
        for block in merged:
            if block["type"] == "output":
                block["folded"] = folded
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": KIND,
        "source": {key: value for key, value in source.items() if value is not None},
        "snippet": {"blocks": merged},
    }
    shown = _links(links)
    if shown:
        document["display"] = {"links": shown}
    return document


def _trim(text: str) -> str:
    """Line endings as ``\\n``, without the trailing newline a print adds."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text[:-1] if text.endswith("\n") else text


class _Recorder:
    """Blocks in the order they were printed. Text of one kind in a row joins."""

    def __init__(self) -> None:
        self.blocks: list[dict[str, Any]] = []
        self._open: str | None = None
        # Set while IPython writes a display or a result, which a terminal prints to stdout
        self.muted = False

    def stream(self, kind: str, text: str) -> None:
        if not text or self.muted:
            return
        if self._open == kind and self.blocks:
            self.blocks[-1]["text"] += text
        else:
            self.blocks.append({"type": kind, "text": text})
            self._open = kind

    def block(self, kind: str, text: str) -> None:
        self._open = None
        self.blocks.append({"type": kind, "text": text})

    def finished(self) -> list[dict[str, Any]]:
        return [{**block, "text": _trim(block["text"])} for block in self.blocks]


class _Tee:
    """A text stream that writes through and records what passes."""

    def __init__(self, target: TextIO, record: Any) -> None:
        self._target = target
        self._record = record

    def write(self, text: str) -> int:
        self._record(text)
        return self._target.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)


@contextmanager
def _teed(recorder: _Recorder) -> Iterator[None]:
    """Record stdout as output and stderr as messages, still printing both."""
    stdout, stderr = sys.stdout, sys.stderr
    sys.stdout = _Tee(stdout, lambda text: recorder.stream("output", text))
    sys.stderr = _Tee(stderr, lambda text: recorder.stream("message", text))
    try:
        yield
    finally:
        sys.stdout, sys.stderr = stdout, stderr


# ---------------------------------------------------------------------------
# Where the code came from


def _git(directory: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _location(path: Path | None) -> dict[str, Any]:
    """The file's path from the git repository's top (else the project root), and the commit."""
    if path is None or not path.is_file():
        return {"file": None, "commit": None}
    path = path.resolve()
    top = _git(path.parent, "rev-parse", "--show-toplevel")
    commit = None
    root: Path | None
    if top is not None:
        root = Path(top).resolve()
        head = _git(path.parent, "rev-parse", "HEAD")
        if head is not None and re.fullmatch(r"[0-9a-f]{40}", head):
            commit = head
    else:
        from framework.project import find_settings_file

        found = find_settings_file(path.parent)
        root = found.parent.resolve() if found is not None else None
    try:
        file = path.relative_to(root).as_posix() if root is not None else path.name
    except ValueError:
        file = path.name
    return {"file": file, "commit": commit}


def _repository(repository: str | None) -> str | None:
    """The argument, else ``publish.repository`` in settings.yml. Sent only when set."""
    if repository is None:
        try:
            from framework.settings import settings_read

            publish = settings_read().get("publish")
            repository = publish.get("repository") if isinstance(publish, dict) else None
        except Exception:
            repository = None
    if repository is None:
        return None
    if not isinstance(repository, str) or not repository.startswith("https://"):
        raise FrameworkError(
            "repository must be an https URL, such as https://github.com/owner/repo."
        )
    return repository


def _source(
    path: Path | None,
    label: str,
    lines: tuple[int, int] | None,
    repository: str | None,
) -> dict[str, Any]:
    location = _location(path)
    return {
        "language": "python",
        "file": location["file"],
        "label": label,
        "lines": list(lines) if lines is not None and location["file"] is not None else None,
        "commit": location["commit"],
        "repository": _repository(repository),
    }


# ---------------------------------------------------------------------------
# Publishing


def _publish(document: dict[str, Any], label: str, options: dict[str, Any]) -> str | list[str]:
    """Publish a snippet document as ``code_snippet.json``."""
    from framework.publish_bundle import artifact_slug
    from framework.storage import publish

    options = dict(options)
    options["slug"] = options.get("slug") or artifact_slug(label)
    options["title"] = options.get("title") or label
    with tempfile.TemporaryDirectory(prefix="fw_snippet_") as temp:
        path = Path(temp) / "code_snippet.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return publish(path, kind=KIND, **options)


_PUBLISH_OPTIONS = (
    "slug",
    "title",
    "description",
    "visibility",
    "password",
    "collection",
    "share_unlisted",
    "to",
    "include_private",
    "display",
)


# ---------------------------------------------------------------------------
# Scripts: with fw.snippet("label"):


@contextmanager
def snippet(
    label: str,
    *,
    output: bool = True,
    folded: bool | None = None,
    links: dict[str, str] | None = None,
    repository: str | None = None,
    publish: bool = True,
    slug: str | None = None,
    title: str | None = None,
    description: str | None = None,
    visibility: str | None = None,
    password: str | None = None,
    collection: str | int | None = None,
    share_unlisted: bool = False,
    to: str | None = None,
    include_private: bool = False,
    display: dict[str, bool] | None = None,
) -> Iterator[None]:
    """Run a block and publish its code and printed output as a code snippet.

    ::

        with fw.snippet("fit-ols"):
            model = smf.ols("score ~ income", data=df).fit()
            print(model.summary())

    The block runs as usual and its output still prints. When it ends, the block's
    code and what it printed to stdout (as output) and stderr (as messages) are
    published to Framework Cloud as a code snippet. A script shows no expression
    values, so none are recorded. If the block raises, the snippet ends with the
    traceback, is published, and the exception continues.

    The code comes from the source file, so ``with fw.snippet()`` needs a script
    or a module, not the interactive prompt. In Jupyter, use the ``%%fw_snippet``
    cell magic instead (``%load_ext framework``).

    The snippet names its source: the file's path from the git repository's top
    (or the project root), the label, the block's lines, and the commit checked
    out. The repository URL is sent only when ``repository`` is given or
    ``publish.repository`` is set in settings.yml. Framework Cloud builds a GitHub
    permalink from a GitHub repository, the commit, the file, and the lines.

    Args:
        label: Names the snippet. The default slug and title come from it.
        output: False publishes the code alone.
        folded: True folds every output block, False unfolds them. Default: Framework
            Cloud folds output longer than 12 lines.
        links: Links shown with the snippet: ``{"notebook": "https://...", "model":
            "model-slug"}``. A model link takes an artifact slug in the same project
            or an https URL.
        repository: Repository URL to name, such as ``"https://github.com/owner/repo"``.
            Default: ``publish.repository`` in settings.yml.
        publish: False records the block without publishing it.
        slug: Artifact slug. Default: from ``label``.
        title: Artifact title. Default: ``label``.
        description: Artifact description.
        visibility: "public", "unlisted", or "private".
        password: Password required to view the snippet.
        collection: Project collection ID or slug.
        share_unlisted: Allow collection readers to see an unlisted snippet.
        to: A configured destination name, or "cloud".
        include_private: Publish without asking about private folders.
        display: What embeds of the snippet show, on Framework Cloud: ``output``,
            ``links``, ``title``, ``description``, and ``author``, such as
            ``{"links": False}``. True shows an item and False hides it; settings left out keep
            their value.
    """
    from framework.cloud import display_settings

    display_settings(display)
    caller = sys._getframe(2)
    filename = caller.f_code.co_filename
    start = caller.f_lineno
    recorder = _Recorder()
    error: BaseException | None = None
    try:
        with _teed(recorder):
            try:
                yield
            except BaseException as raised:
                error = raised
                raise
    finally:
        code, lines = _block_code(filename, start)
        if error is not None and not isinstance(error, (KeyboardInterrupt, GeneratorExit)):
            text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            recorder.block("error", text)
        if code is None:
            message = (
                f'fw.snippet("{label}") could not read its code from {filename}. '
                "Use it in a script or module, or %%fw_snippet in Jupyter."
            )
            if error is None:
                raise FrameworkError(message)
            inform(message)
        elif publish:
            path = None if _cell_file(filename) else Path(filename)
            if path is None or not path.is_file():
                path = _notebook_path()
            if path is not None and path.suffix in _NOTEBOOK_SUFFIXES:
                lines = None
            document = _document(
                [{"type": "code", "text": code}, *recorder.finished()],
                _source(path, label, lines, repository),
                output=output,
                folded=folded,
                links=links,
            )
            options = {
                "slug": slug,
                "title": title,
                "description": description,
                "visibility": visibility,
                "password": password,
                "collection": collection,
                "share_unlisted": share_unlisted,
                "to": to,
                "include_private": include_private,
                "display": display,
            }
            if document is not None:
                if error is None:
                    _publish(document, label, options)
                else:
                    try:
                        _publish(document, label, options)
                    except Exception as failed:
                        inform(f"The snippet was not published: {failed}")


def _cell_file(filename: str) -> bool:
    """Whether ``filename`` names a Jupyter cell rather than a file on disk."""
    return filename.startswith("<") or "ipykernel_" in filename


def _block_code(filename: str, start: int) -> tuple[str | None, tuple[int, int] | None]:
    """The body of the ``with`` statement on line ``start``, dedented, and its lines."""
    text = "".join(linecache.getlines(filename))
    if not text:
        return None, None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None, None
    found: ast.With | ast.AsyncWith | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.With, ast.AsyncWith))
            and node.lineno <= start <= (node.body[0].lineno - 1 if node.body else node.lineno)
            and (found is None or node.lineno > found.lineno)
        ):
            found = node
    if found is None or not found.body:
        return None, None
    first = found.body[0].lineno
    decorated = getattr(found.body[0], "decorator_list", None)
    if decorated:
        first = min(first, *(item.lineno for item in decorated))
    last = found.end_lineno or found.body[-1].lineno
    lines = text.splitlines()[first - 1 : last]
    return _dedent(lines), (first, last)


def _dedent(lines: list[str]) -> str:
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(indents, default=0)
    return "\n".join(line[cut:] for line in lines).strip("\n")


# ---------------------------------------------------------------------------
# Jupyter: %%fw_snippet


def _notebook_path() -> Path | None:
    """The running notebook's file, when the front end says."""
    candidates: list[str] = []
    try:
        from IPython import get_ipython

        shell = get_ipython()
    except ImportError:
        shell = None
    if shell is not None:
        vscode = shell.user_ns.get("__vsc_ipynb_file__")
        if isinstance(vscode, str):
            candidates.append(vscode)
    session = os.environ.get("JPY_SESSION_NAME")
    if session:
        candidates.append(session)
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def _find_marker(label: str) -> list[Path]:
    """Notebooks in the working folder whose code holds ``%%fw_snippet <label>``."""
    pattern = re.compile(r"%%fw_snippet\s+" + re.escape(label) + r"(?![\w-])")
    found = []
    for path in sorted(Path.cwd().iterdir()):
        if path.suffix not in _NOTEBOOK_SUFFIXES or not path.is_file():
            continue
        try:
            if pattern.search(path.read_text(encoding="utf-8")):
                found.append(path)
        except (OSError, UnicodeError):
            continue
    return found


def _cell_lines(path: Path, label: str) -> tuple[int, int] | None:
    """In a .qmd file, the lines of the code after ``%%fw_snippet <label>``."""
    if path.suffix != ".qmd":
        return None
    text = path.read_text(encoding="utf-8").splitlines()
    pattern = re.compile(r"^\s*%%fw_snippet\s+" + re.escape(label) + r"(?![\w-])")
    starts = [index for index, line in enumerate(text) if pattern.match(line)]
    if len(starts) != 1:
        return None
    first = starts[0] + 1
    last = first
    for index in range(first, len(text)):
        if text[index].lstrip().startswith("```"):
            break
        last = index
    while last > first and not text[last].strip():
        last -= 1
    while first < last and (not text[first].strip() or _OPTION_LINE.match(text[first])):
        first += 1
    return (first + 1, last + 1)


def _cell_code(cell: str) -> str:
    """The cell's code without Quarto option lines."""
    lines = [line for line in cell.splitlines() if not _OPTION_LINE.match(line)]
    return "\n".join(lines).strip("\n")


def _magic_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(prog="%%fw_snippet", add_help=False, exit_on_error=False)
    parser.add_argument("label")
    fold = parser.add_mutually_exclusive_group()
    fold.add_argument("--folded", dest="folded", action="store_const", const=True)
    fold.add_argument("--unfolded", dest="folded", action="store_const", const=False)
    parser.add_argument("--no-output", dest="output", action="store_false")
    parser.add_argument("--link", action="append", default=[])
    parser.add_argument("--repository")
    parser.add_argument("--file")
    parser.add_argument("--no-publish", dest="publish", action="store_false")
    parser.add_argument("--slug")
    parser.add_argument("--title")
    parser.add_argument("--description")
    parser.add_argument("--visibility")
    parser.add_argument("--password")
    parser.add_argument("--collection")
    parser.add_argument("--share-unlisted", action="store_true")
    parser.add_argument("--to")
    parser.add_argument("--include-private", action="store_true")
    parser.add_argument("--display", action="append", default=[])
    return parser


def _magic_arguments(line: str) -> Any:
    import argparse
    import shlex

    try:
        arguments = _magic_parser().parse_args(shlex.split(line))
    except (argparse.ArgumentError, SystemExit, ValueError) as error:
        raise FrameworkError(
            f"%%fw_snippet {line}: {error}. Usage: %%fw_snippet LABEL [--folded | --unfolded] "
            "[--no-output] [--link notebook=URL] [--link model=SLUG] [--repository URL] "
            "[--slug SLUG] [--title TITLE] [--visibility VISIBILITY] [--to DESTINATION] "
            "[--display SETTING=on|off] [--no-publish]"
        ) from None
    links: dict[str, str] = {}
    for link in arguments.link:
        rel, separator, value = link.partition("=")
        if not separator:
            raise FrameworkError("--link takes REL=VALUE, as in --link notebook=https://...")
        links[rel] = value
    arguments.links = links
    display: dict[str, bool] = {}
    for setting in arguments.display:
        name, separator, value = setting.partition("=")
        if not separator or value not in ("on", "off"):
            raise FrameworkError(
                "--display takes SETTING=on or SETTING=off, as in --display links=off"
            )
        display[name] = value == "on"
    arguments.display = display or None
    from framework.cloud import display_settings

    display_settings(arguments.display)
    return arguments


def run_cell_snippet(shell: Any, line: str, cell: str) -> dict[str, Any] | None:
    """Run a cell for ``%%fw_snippet``, show its outputs, and publish them.

    Returns the snippet document, or None when the cell has no code.
    """
    arguments = _magic_arguments(line)
    code = _cell_code(cell)
    recorder = _Recorder()
    error: dict[str, Any] = {}

    publisher = shell.display_pub
    original_publish = publisher.publish
    hook = shell.displayhook
    original_write = hook.write_format_data
    original_quiet = hook.quiet
    original_start = hook.start_displayhook
    original_finish = hook.finish_displayhook
    original_showtraceback = shell._showtraceback

    def plain(data: dict[str, Any]) -> str | None:
        if any(str(key).startswith("image/") for key in data):
            return None
        text = data.get("text/plain")
        return text if isinstance(text, str) else None

    def publish_display(data: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        text = plain(data or {})
        if text is not None:
            recorder.block("output", text)
        muted, recorder.muted = recorder.muted, True
        try:
            return original_publish(data, *args, **kwargs)
        finally:
            recorder.muted = muted

    def start_result() -> Any:
        recorder.muted = True
        return original_start()

    def finish_result() -> Any:
        try:
            return original_finish()
        finally:
            recorder.muted = False

    def write_result(format_dict: dict[str, Any], md_dict: Any = None) -> Any:
        text = plain(format_dict or {})
        if text is not None:
            recorder.block("output", text)
        return original_write(format_dict, md_dict)

    def quiet() -> bool:
        try:
            return bool(hook.semicolon_at_end_of_expression(cell))
        except Exception:
            return False

    def record_traceback(etype: Any, evalue: Any, stb: list[str]) -> None:
        error["text"] = "\n".join(stb)
        error["value"] = evalue

    publisher.publish = publish_display
    hook.write_format_data = write_result
    hook.quiet = quiet
    hook.start_displayhook = start_result
    hook.finish_displayhook = finish_result
    shell._showtraceback = record_traceback
    try:
        with _teed(recorder):
            result = shell.run_cell(cell, store_history=False)
    finally:
        publisher.publish = original_publish
        hook.write_format_data = original_write
        hook.quiet = original_quiet
        hook.start_displayhook = original_start
        hook.finish_displayhook = original_finish
        shell._showtraceback = original_showtraceback

    raised = result.error_in_exec or result.error_before_exec
    if "text" in error:
        recorder.block("error", error["text"])
    elif raised is not None:
        recorder.block("error", "".join(traceback.format_exception_only(type(raised), raised)))

    path = Path(arguments.file) if arguments.file else _notebook_path()
    if path is None:
        matches = _find_marker(arguments.label)
        path = matches[0] if len(matches) == 1 else None
    lines = _cell_lines(path, arguments.label) if path is not None else None
    document = _document(
        [{"type": "code", "text": code}, *recorder.finished()],
        _source(path, arguments.label, lines, arguments.repository),
        output=arguments.output,
        folded=arguments.folded,
        links=arguments.links,
    )
    if document is not None and arguments.publish:
        options = {name: getattr(arguments, name) for name in _PUBLISH_OPTIONS}
        if raised is None:
            _publish_in_cell(document, arguments.label, options)
        else:
            try:
                _publish_in_cell(document, arguments.label, options)
            except Exception as failed:
                inform(f"The snippet was not published: {failed}")
    if raised is not None:
        # The traceback was recorded instead of shown, so the cell fails once, here.
        raise raised
    return document


# Marks the publishing status a cell shows, so fw snippet publish leaves it out
STATUS_METADATA = {"framework": {"role": "snippet-status"}}


def _publish_in_cell(
    document: dict[str, Any], label: str, options: dict[str, Any]
) -> str | list[str]:
    """Publish, and show Framework's messages as a display the snippet readers skip."""
    import logging

    from IPython.display import publish_display_data

    from framework._log import logger

    messages: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handlers, level = logger.handlers[:], logger.level
    logger.handlers = [Collect()]
    logger.setLevel(logging.INFO)
    try:
        return _publish(document, label, options)
    finally:
        logger.handlers = handlers
        logger.setLevel(level)
        if messages:
            publish_display_data(  # type: ignore[no-untyped-call]
                {"text/plain": "\n".join(messages)}, metadata=STATUS_METADATA
            )


def load_ipython_extension(shell: Any) -> None:
    """``%load_ext framework``: add the ``%%fw_snippet`` magic and show Framework's messages."""
    from framework._log import show_messages

    show_messages()

    def fw_snippet(line: str, cell: str) -> None:
        """Run the cell and publish its code and printed output as a code snippet.

        %%fw_snippet LABEL [--folded | --unfolded] [--no-output]
            [--link notebook=URL] [--link model=SLUG] [--repository URL] [--file PATH]
            [--slug SLUG] [--title TITLE] [--description TEXT] [--visibility VISIBILITY]
            [--password PASSWORD] [--collection COLLECTION] [--share-unlisted]
            [--to DESTINATION] [--include-private] [--display SETTING=on|off] [--no-publish]
        """
        run_cell_snippet(shell, line, cell)

    shell.register_magic_function(fw_snippet, magic_kind="cell", magic_name="fw_snippet")
