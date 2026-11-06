from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

import framework as fw
from framework import cloud
from framework._pages import github_remote, pages_base, pages_slug

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

REMOTE = "https://github.com/Owner/study.git"


def git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.fixture
def repo(project: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The project as a git repository whose GitHub remote pushes to a local bare repo."""
    os.environ.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="T",
        GIT_AUTHOR_EMAIL="t@example.com",
        GIT_COMMITTER_NAME="T",
        GIT_COMMITTER_EMAIL="t@example.com",
    )
    bare = tmp_path_factory.mktemp("remote") / "study.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    git(project, "init", "-q", "-b", "main")
    git(project, "remote", "add", "origin", REMOTE)
    # get-url still shows the GitHub URL; pushes go to the bare repository
    git(project, "config", f"url.{bare.as_uri()}.pushInsteadOf", REMOTE)
    git(project, "config", "test.bare", str(bare))
    git(project, "add", "settings.yml")
    git(project, "commit", "-qm", "init")
    return project


def bare_log(repo: Path, branch: str = "main") -> list[str]:
    bare = git(repo, "config", "test.bare")
    return git(repo, "--git-dir", bare, "log", "--format=%s", branch).splitlines()


def test_remote_and_url_helpers() -> None:
    assert github_remote("https://github.com/Owner/study.git") == ("Owner", "study")
    assert github_remote("git@github.com:Owner/study") == ("Owner", "study")
    assert github_remote("ssh://git@github.com/Owner/study.git/") == ("Owner", "study")
    assert github_remote("https://gitlab.com/Owner/study.git") is None
    assert pages_base("Owner", "study") == "https://owner.github.io/study/"
    assert pages_base("Owner", "owner.github.io") == "https://owner.github.io/"
    assert pages_slug(Path("Final Report (v2).html")) == "final-report-v2"
    assert pages_slug(Path("outputs/Site")) == "site"


def test_publish_commits_only_the_page_and_pushes(
    repo: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="framework")
    report = repo / "outputs/Final Report (v2).html"
    report.parent.mkdir()
    report.write_text('<img src="Final Report (v2)_files/a.png">', encoding="utf-8")
    (repo / "outputs/Final Report (v2)_files").mkdir()
    (repo / "outputs/Final Report (v2)_files/a.png").write_bytes(b"png")
    (repo / "notes.txt").write_text("mine", encoding="utf-8")
    git(repo, "add", "notes.txt")

    url = fw.publish(report, to="github")
    assert url == "https://owner.github.io/study/final-report-v2/"
    err = capsys.readouterr().err
    assert "GitHub Pages sites are public, even when the repository is private." in err
    assert f"Published to {url}" in caplog.text

    page = repo / "published/final-report-v2"
    assert (page / "index.html").is_file()
    assert (page / "Final Report (v2)_files/a.png").read_bytes() == b"png"
    committed = git(repo, "show", "--name-only", "--format=%s", "HEAD").splitlines()
    assert committed[0] == "Publish final-report-v2"
    assert "published/final-report-v2/artifact.json" in committed
    assert "published/final-report-v2/v/1/index.html" in committed
    assert "published/index.html" in committed
    assert "notes.txt" not in committed
    # What the user had staged stays staged and out of the commit
    assert git(repo, "diff", "--cached", "--name-only") == "notes.txt"
    assert bare_log(repo) == ["Publish final-report-v2", "init"]

    # Every publish preserves its own immutable version.
    fw.publish(report, to="github")
    assert (page / "v/2/index.html").is_file()
    assert bare_log(repo) == ["Publish final-report-v2", "Publish final-report-v2", "init"]

    # A new version replaces the folder completely
    shutil.rmtree(repo / "outputs/Final Report (v2)_files")
    report.write_text("<p>v3</p>", encoding="utf-8")
    fw.publish(report, to="github", slug="final-report-v2")
    assert (page / "v/1/Final Report (v2)_files/a.png").read_bytes() == b"png"
    assert not (page / "Final Report (v2)_files").exists()
    assert (page / "v/3/index.html").read_text() == "<p>v3</p>"


def test_folder_needs_an_index(repo: Path) -> None:
    site = repo / "outputs/site"
    site.mkdir(parents=True)
    (site / "page.html").write_text("<p>hi</p>", encoding="utf-8")
    assert fw.publish(site, to="github") == "https://owner.github.io/study/site/"
    assert 'href="page.html"' in (repo / "published/site/index.html").read_text()
    (site / "index.html").write_text("<p>home</p>", encoding="utf-8")
    assert fw.publish(site, to="github") == "https://owner.github.io/study/site/"
    assert (repo / "published/site/page.html").is_file()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"dest": "x"}, 'Not used with to="github": dest'),
        ({"slug": True}, "slug=True/False"),
        ({"to": "gitlab"}, 'to must be "cloud", "bucket", "github", or None'),
    ],
)
def test_bad_arguments(repo: Path, kwargs: dict[str, Any], message: str) -> None:
    report = repo / "report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match=message):
        fw.publish(report, **{"to": "github", **kwargs})


def test_needs_a_github_repository(project: Path) -> None:
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    report = project / "report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    git(project, "init", "-q")
    with pytest.raises(fw.FrameworkError, match="no GitHub remote"):
        fw.publish(report, to="github")
    git(project, "remote", "add", "origin", "https://gitlab.com/o/r.git")
    with pytest.raises(fw.FrameworkError, match="no GitHub remote"):
        fw.publish(report, to="github")


def test_interactive_sessions_confirm(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = repo / "report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    with pytest.raises(fw.FrameworkError, match="Cancelled"):
        fw.publish(report, to="github")
    assert not (repo / "published").exists()
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert fw.publish(report, to="github").endswith("/report/")


def test_other_branches_warn(repo: Path) -> None:
    git(repo, "push", "-q", "origin", "main")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git(repo, "switch", "-q", "-c", "draft")
    report = repo / "report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    with pytest.warns(fw.FrameworkWarning, match="deploys only from the default branch"):
        fw.publish(report, to="github")
    assert bare_log(repo, "draft")[0] == "Publish report"


@pytest.mark.parametrize("pages_url", ["https://pages.example.org/study/", None])
def test_the_cloud_knows_the_pages_url(
    repo: Path, monkeypatch: pytest.MonkeyPatch, pages_url: str | None
) -> None:
    def me(request: httpx.Request) -> httpx.Response:
        body = {"kind": "project", "project": "study", "pages_url": pages_url}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(me))
    (repo / ".env").write_text(
        "FW_PROJECT_TOKEN=fwp_project0123\nFW_CLOUD_URL=https://cloud.example.edu\n",
        encoding="utf-8",
    )
    report = repo / "report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    if pages_url is None:
        with pytest.warns(fw.FrameworkWarning, match="GitHub Pages is off"):
            url = fw.publish(report, to="github")
        assert url == "https://owner.github.io/study/report/"
    else:
        assert fw.publish(report, to="github") == "https://pages.example.org/study/report/"


def test_publish_notebook_html_only(repo: Path) -> None:
    notebook = repo / "analysis.qmd"
    notebook.write_text("# hi\n", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="HTML only"):
        fw.publish_notebook(notebook, to="github", format="pdf")


def test_pages_size_counts_existing_archives(tmp_path: Path) -> None:
    from framework._pages import _directory_bytes

    (tmp_path / "artifact/v/1").mkdir(parents=True)
    (tmp_path / "artifact/index.html").write_bytes(b"new")
    (tmp_path / "artifact/v/1/index.html").write_bytes(b"older version")
    assert _directory_bytes(tmp_path) == len(b"newolder version")


def test_pages_size_limit_prevents_writes_commit_and_push(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = repo / "report.html"
    report.write_text("<p>hello</p>", encoding="utf-8")
    original_head = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(
        "framework._pages._directory_bytes",
        lambda path: 1_000_000_001 if path == repo / "published" else 50,
    )
    with pytest.raises(fw.FrameworkError, match="exceed the 1 GB"):
        fw.publish(report, to="github")
    assert not (repo / "published").exists()
    assert git(repo, "rev-parse", "HEAD") == original_head
    bare = git(repo, "config", "test.bare")
    assert git(repo, "--git-dir", bare, "for-each-ref", "--format=%(refname)") == ""


def test_pages_size_warns_before_publishing(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = repo / "report.html"
    report.write_text("<p>hello</p>", encoding="utf-8")
    monkeypatch.setattr(
        "framework._pages._directory_bytes",
        lambda path: 800_000_000 if path == repo / "published" else 50,
    )
    with pytest.warns(fw.FrameworkWarning, match="approaching its 1 GB"):
        assert fw.publish(report, to="github") == "https://owner.github.io/study/report/"
    assert (repo / "published/report/v/1/index.html").is_file()
