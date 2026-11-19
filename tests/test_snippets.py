"""Code snippets: the %%fw_snippet cell magic and fw.snippet() in scripts."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import framework as fw
from framework import snippets
from framework.errors import FrameworkError
from tests.test_cloud import FakeCloud, cloud_project, server  # noqa: F401

IPython = pytest.importorskip("IPython")


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
    """Snippets that would be published, instead of publishing them."""
    calls: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    monkeypatch.setattr(
        snippets,
        "_publish",
        lambda document, label, options: calls.append((document, label, options)),
    )
    return calls


@pytest.fixture
def shell() -> Iterator[Any]:
    from IPython.core.interactiveshell import InteractiveShell

    instance = InteractiveShell.instance()
    instance.extension_manager.load_extension("framework")
    yield instance
    InteractiveShell.clear_instance()


def blocks(document: dict[str, Any]) -> list[tuple[str, str]]:
    return [(block["type"], block["text"]) for block in document["snippet"]["blocks"]]


# The document -------------------------------------------------------------------------


def test_document_joins_adjacent_output_and_folds() -> None:
    document = snippets._document(
        [
            {"type": "code", "text": "x"},
            {"type": "output", "text": "1"},
            {"type": "output", "text": "2"},
            {"type": "message", "text": "note"},
        ],
        {"language": "python", "label": "a", "file": None},
        folded=True,
        links={"notebook": "https://example.org/n", "model": "fit"},
    )
    assert document is not None
    assert document["source"] == {"language": "python", "label": "a"}
    assert document["snippet"]["blocks"] == [
        {"type": "code", "text": "x"},
        {"type": "output", "text": "1\n2", "folded": True},
        {"type": "message", "text": "note"},
    ]
    assert document["display"]["links"] == [
        {"rel": "notebook", "url": "https://example.org/n"},
        {"rel": "model", "artifact": "fit"},
    ]
    assert "width" not in document["snippet"]


def test_document_without_output_keeps_the_code() -> None:
    document = snippets._document(
        [{"type": "code", "text": "x"}, {"type": "output", "text": "1"}],
        {"language": "python"},
        output=False,
    )
    assert document is not None
    assert blocks(document) == [("code", "x")]


def test_document_without_code_is_none() -> None:
    assert snippets._document([{"type": "code", "text": ""}], {"language": "python"}) is None


@pytest.mark.parametrize(
    ("links", "message"),
    [
        ({"blog": "https://x"}, 'named "notebook" or "model"'),
        ({"notebook": "http://x"}, "notebook link takes an https URL"),
        ({"model": "not a slug"}, "artifact slug"),
    ],
)
def test_links_are_checked(links: dict[str, str], message: str) -> None:
    with pytest.raises(FrameworkError, match=message):
        snippets._links(links)


def test_repository_comes_from_settings(project: Path) -> None:
    (project / "settings.yml").write_text(
        "default:\n  publish:\n    repository: https://github.com/owner/repo\n", encoding="utf-8"
    )
    assert snippets._repository(None) == "https://github.com/owner/repo"
    assert snippets._repository("https://github.com/other/repo") == "https://github.com/other/repo"
    with pytest.raises(FrameworkError, match="https URL"):
        snippets._repository("git@github.com:owner/repo")


def test_no_repository_by_default(project: Path) -> None:
    assert snippets._repository(None) is None


def test_location_in_git(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    folder = tmp_path / "notebooks"
    folder.mkdir()
    path = folder / "a.py"
    path.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.org",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
    )
    location = snippets._location(path)
    assert location["file"] == "notebooks/a.py"
    assert len(location["commit"]) == 40


# %%fw_snippet --------------------------------------------------------------------------


def test_magic_records_streams_displays_and_the_result(
    shell: Any, published: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    shell.run_cell(
        textwrap.dedent(
            """\
            %%fw_snippet fit-ols --folded --link notebook=https://example.org/n
            #| echo: true
            import sys
            from IPython.display import display
            print("hello")
            print("warn", file=sys.stderr)
            display({"a": 1})
            40 + 2
            """
        )
    )
    document, label, options = published[-1]
    assert label == "fit-ols"
    assert options["slug"] is None and options["to"] is None
    assert blocks(document) == [
        (
            "code",
            'import sys\nfrom IPython.display import display\nprint("hello")\n'
            'print("warn", file=sys.stderr)\ndisplay({"a": 1})\n40 + 2',
        ),
        ("output", "hello"),
        ("message", "warn"),
        ("output", "{'a': 1}\n42"),
    ]
    assert document["snippet"]["blocks"][1]["folded"] is True
    assert document["source"]["language"] == "python"
    assert document["source"]["label"] == "fit-ols"
    assert document["display"]["links"] == [{"rel": "notebook", "url": "https://example.org/n"}]
    out = capsys.readouterr()
    assert "hello" in out.out and "42" in out.out
    assert "warn" in out.err


def test_magic_skips_outputs_with_an_image(shell: Any, published: list[Any]) -> None:
    shell.run_cell(
        "%%fw_snippet plot\n"
        "from IPython.display import publish_display_data\n"
        "publish_display_data({'image/png': 'iVBOR', 'text/plain': '<Figure size 640x480>'})\n"
        "print('done')"
    )
    document = published[-1][0]
    assert blocks(document)[1:] == [("output", "done")]


def test_magic_respects_a_trailing_semicolon(shell: Any, published: list[Any]) -> None:
    shell.run_cell("%%fw_snippet quiet\n1 + 1;")
    assert blocks(published[-1][0]) == [("code", "1 + 1;")]


def test_magic_without_output(shell: Any, published: list[Any]) -> None:
    shell.run_cell("%%fw_snippet bare --no-output\nprint('x')")
    assert blocks(published[-1][0]) == [("code", "print('x')")]


def test_magic_publishes_an_error_and_fails_the_cell(
    shell: Any, published: list[Any], capsys: pytest.CaptureFixture[str]
) -> None:
    result = shell.run_cell("%%fw_snippet boom\nprint('before')\n1 / 0")
    assert isinstance(result.error_in_exec, ZeroDivisionError)
    kinds = blocks(published[-1][0])
    assert kinds[:2] == [("code", "print('before')\n1 / 0"), ("output", "before")]
    assert kinds[2][0] == "error"
    assert "ZeroDivisionError" in kinds[2][1]
    # The traceback shows once, for the magic's cell
    assert capsys.readouterr().out.count("division by zero") == 1


def test_magic_without_publish(shell: Any, published: list[Any]) -> None:
    shell.run_cell("%%fw_snippet dry --no-publish\nprint('x')")
    assert published == []


def test_magic_rejects_bad_arguments(shell: Any, published: list[Any]) -> None:
    result = shell.run_cell("%%fw_snippet\nprint('x')")
    assert isinstance(result.error_in_exec, FrameworkError)
    assert "Usage: %%fw_snippet LABEL" in str(result.error_in_exec)
    assert published == []


def test_magic_finds_its_quarto_file_and_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: Any, published: list[Any]
) -> None:
    (tmp_path / "analysis.qmd").write_text(
        "---\ntitle: t\n---\n\n```{python}\n#| label: fit\n"
        "%%fw_snippet fit\nx = 1\nprint(x)\n```\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JPY_SESSION_NAME", raising=False)
    shell.run_cell("%%fw_snippet fit\nx = 1\nprint(x)")
    source = published[-1][0]["source"]
    assert source["file"] == "analysis.qmd"
    assert source["lines"] == [8, 9]


def test_magic_uses_the_jupyter_session_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shell: Any, published: list[Any]
) -> None:
    notebook = tmp_path / "work.ipynb"
    notebook.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("JPY_SESSION_NAME", str(notebook))
    shell.run_cell("%%fw_snippet a\nprint(1)")
    source = published[-1][0]["source"]
    assert source["file"] == "work.ipynb"
    assert "lines" not in source


def test_magic_publishes_code_snippet_json(
    cloud_project: Path,  # noqa: F811
    server: FakeCloud,  # noqa: F811
    shell: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    server.route(
        "POST",
        "/api/v1/documents",
        lambda r: httpx.Response(
            200, json={"id": 3, "url": "https://x/d/fit-ols", "version": 2, "unchanged": True}
        ),
    )
    shell.run_cell("%%fw_snippet fit-ols --to cloud\nprint('hi')")
    body = server.requests[-1].content
    assert b'filename="code_snippet.json"' in body
    assert b'name="kind"\r\n\r\ncode_snippet' in body
    assert b'name="slug"\r\n\r\nfit-ols' in body
    assert b'name="title"\r\n\r\nfit-ols' in body
    assert "Unchanged, still version 2: https://x/d/fit-ols" in caplog.text


# with fw.snippet() ---------------------------------------------------------------------


def run_script(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "analysis.py"
    script.write_text(
        textwrap.dedent(
            """\
            import json, sys
            import framework as fw
            from framework import snippets

            snippets._publish = lambda document, label, options: print(
                "DOC " + json.dumps(document), file=sys.__stdout__
            )
            """
        )
        + textwrap.dedent(body),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, cwd=tmp_path, check=False
    )


def script_document(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    line = next(line for line in result.stdout.splitlines() if line.startswith("DOC "))
    return json.loads(line[4:])


def test_script_block_publishes_its_code_and_output(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        """\
        x = 1
        with fw.snippet("fit", folded=False):
            y = x + 1
            print("y is", y)
            print("note", file=sys.stderr)
        print("outside")
        """,
    )
    assert result.returncode == 0, result.stderr
    document = script_document(result)
    assert blocks(document) == [
        ("code", 'y = x + 1\nprint("y is", y)\nprint("note", file=sys.stderr)'),
        ("output", "y is 2"),
        ("message", "note"),
    ]
    assert document["snippet"]["blocks"][1]["folded"] is False
    assert document["source"]["file"] == "analysis.py"
    assert document["source"]["lines"] == [10, 12]
    assert "y is 2" in result.stdout and "outside" in result.stdout


def test_script_block_publishes_an_error_and_raises(tmp_path: Path) -> None:
    result = run_script(
        tmp_path,
        """\
        with fw.snippet("boom"):
            print("before")
            raise ValueError("bad value")
        """,
    )
    assert result.returncode == 1
    assert "ValueError: bad value" in result.stderr
    kinds = blocks(script_document(result))
    assert kinds[1] == ("output", "before")
    assert kinds[2][0] == "error" and "ValueError: bad value" in kinds[2][1]


def test_snippet_is_exported() -> None:
    assert "snippet" in fw.__all__
    assert callable(fw.load_ipython_extension)


def test_magic_shows_the_publishing_status_as_marked_display(
    shell: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[tuple[dict[str, Any], Any]] = []
    monkeypatch.setattr(
        snippets,
        "_publish",
        lambda document, label, options: snippets.inform("Published version 2: https://x/d/a"),
    )
    original = shell.display_pub.publish

    def record(data: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        shown.append((data, kwargs.get("metadata")))
        return original(data, *args, **kwargs)

    monkeypatch.setattr(shell.display_pub, "publish", record)
    shell.run_cell("%%fw_snippet status\nprint(1)")
    assert shown[-1] == (
        {"text/plain": "Published version 2: https://x/d/a"},
        {"framework": {"role": "snippet-status"}},
    )
