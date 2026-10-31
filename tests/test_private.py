from __future__ import annotations

import sys
from pathlib import Path

import pytest

import framework as fw
from framework._private import confirm_private_upload, is_private_path


def test_full_path_match_ignores_case(tmp_path: Path) -> None:
    assert is_private_path(tmp_path / "private/x.html")
    assert is_private_path(tmp_path / "proj/Private/out.html")
    assert is_private_path(tmp_path / "my_PRIVATE_notes.csv")
    assert not is_private_path(tmp_path / "public/x.html")


# The same cases as R's test-publish.R
@pytest.mark.parametrize(
    "path",
    [
        "/private/var/folders/ab/report.html",
        "/srv/project/outputs/public/report.html",
    ],
)
def test_silent_paths(path: str) -> None:
    # macOS resolves /tmp and /var to /private/..., which the user did not name
    assert not is_private_path(Path(path))


@pytest.mark.parametrize(
    "path",
    [
        "/private/var/folders/proj/Private/out.html",
        "~/proj/Private/out.html",
        "/home/PRIVATE-studies/report.html",
        "/srv/my_private_notes.html",
    ],
)
def test_flagged_paths(path: str) -> None:
    assert is_private_path(Path(path))


def test_no_terminal_needs_include_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = tmp_path / "private/ids.csv"
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        confirm_private_upload([secret], include_private=False)
    assert str(secret) in capsys.readouterr().err
    confirm_private_upload([secret], include_private=True)
    confirm_private_upload([tmp_path / "public.csv"], include_private=False)


@pytest.mark.parametrize(("answer", "uploads"), [("y", True), ("", False), ("no", False)])
def test_a_terminal_asks_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, uploads: bool
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: answer)
    secret = [tmp_path / "private/ids.csv"]
    if uploads:
        confirm_private_upload(secret, include_private=False)
    else:
        with pytest.raises(fw.FrameworkError, match="Cancelled"):
            confirm_private_upload(secret, include_private=False)
