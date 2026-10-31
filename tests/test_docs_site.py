"""The docs site archive for py.framework.pub (scripts/build_docs.py)."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from scripts import build_docs


def test_docstring_sections_are_parsed() -> None:
    parsed = build_docs.parse_docstring(
        "Read a file.\n\nFirst paragraph.\n\nSecond.\n\nThird.\n\n"
        "Args:\n    path: Where it is.\n        Continued.\n    **kwargs: Passed on.\n\n"
        "Returns:\n    A frame.\n"
    )
    assert parsed["summary"] == "Read a file."
    assert parsed["description"] == "First paragraph.\n\nSecond."
    assert parsed["details"] == "Third."
    items = build_docs.parse_items(parsed["sections"]["Args"])
    assert items == [("path", "Where it is. Continued."), ("**kwargs", "Passed on.")]
    assert parsed["sections"]["Returns"] == ["A frame."]


def test_code_spans_become_markdown_and_link_exported_names() -> None:
    text = build_docs._markdown("See ``data_read()`` and ``options.x``.")
    assert text == "See [`data_read()`](ref:data_read) and `options.x`."


def test_the_site_builds_and_validates(tmp_path: Path) -> None:
    archive = tmp_path / "docs-site.tar.gz"
    reference = tmp_path / "reference.json"
    assert build_docs.main(["--reference", str(reference), "--archive", str(archive)]) == 0

    data = json.loads(reference.read_text(encoding="utf-8"))
    import framework

    public = {name for name in framework.__all__ if not name.startswith("__")}
    assert {entry["name"] for entry in data["entries"]} == public
    assert data["version"] == framework.__version__

    with tarfile.open(archive) as bundle:
        names = bundle.getnames()
    assert "docs-site/site.yml" in names
    assert "docs-site/reference.json" in names
    assert all(name.startswith("docs-site/") for name in names)
    assert "docs-site/categories.yml" not in names


def test_an_undocumented_parameter_is_reported() -> None:
    def sample(name: str, *, extra: int = 1) -> None:
        """Do a thing.

        Args:
            name: The name.
        """

    problems: list[str] = []
    entry = build_docs.build_entry("sample", sample, "workflow", problems)
    assert problems == ["sample: parameter 'extra' is not documented"]
    assert entry["usage"] == "sample(name: str, *, extra: int = 1) -> None"
    assert entry["parameters"][0] == {
        "name": "name",
        "description": "The name.",
        "type": "str",
        "required": True,
    }


@pytest.mark.parametrize("missing", ["data_read"])
def test_every_export_needs_a_category(missing: str) -> None:
    categories = [{"id": "data", "title": "Data", "names": ["data_save"]}]
    with pytest.raises(build_docs.DocsError, match=missing):
        build_docs.build_reference(categories, [])


def test_code_blocks_are_not_headings_or_links() -> None:
    text = "## Use\n\n```sh\n# a comment\n[x](guide:nowhere)\n```\n\nText.\n"
    assert build_docs._prose(text) == "## Use\n\n\n\nText.\n"
