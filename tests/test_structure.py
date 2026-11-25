"""Project structure artifacts: fw.structure() and fw.publish_structure(), kind tree."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

import framework as fw
from framework.errors import FrameworkError
from tests.test_cloud import FakeCloud, cloud_project, server  # noqa: F401

# framework.structure, the module: fw.structure is the function
st = importlib.import_module("framework.structure")

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

LEAKED = ("a.csv", "b.csv", "ids.csv", "fit.rds", "draws.rds")

# The shared fixture from Framework Cloud's structure artifacts plan, which every client
# passes: its published nodes, in any order
EXPECTED = [
    {"path": ".gitignore", "type": "file"},
    {
        "path": "",
        "type": "ignored",
        "ignored": {"rule": ".env", "source": ".gitignore", "line": 3},
        "hidden": 1,
    },
    {"path": "README.md", "type": "file"},
    {"path": "R", "type": "dir"},
    {"path": "R/clean.R", "type": "file"},
    {"path": "data", "type": "dir"},
    {
        "path": "data/raw",
        "type": "dir",
        "ignored": {"rule": "data/raw/", "source": ".gitignore", "line": 1},
        "hidden": 2,
    },
    {"path": "data/private", "type": "dir", "private": True, "hidden": 1},
    {"path": "outputs", "type": "dir"},
    {
        "path": "outputs",
        "type": "ignored",
        "ignored": {"rule": "*.rds", "source": ".gitignore", "line": 2},
        "hidden": 2,
    },
    {"path": "outputs/table.csv", "type": "file"},
]


def write_fixture(root: Path, env: bool = True) -> None:
    (root / ".gitignore").write_text("data/raw/\n*.rds\n.env\n", encoding="utf-8")
    files = [
        "README.md",
        "R/clean.R",
        "data/raw/a.csv",
        "data/raw/b.csv",
        "data/private/ids.csv",
        "outputs/fit.rds",
        "outputs/draws.rds",
        "outputs/table.csv",
    ]
    if env:
        files.append(".env")
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture(autouse=True)
def plain_git(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Git without the user's global excludes. Returns the global config file."""
    config = tmp_path_factory.mktemp("gitconfig") / "config"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return config


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The shared fixture in a git work tree, as the current folder."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    root = tmp_path / "my-project"
    root.mkdir()
    write_fixture(root)
    git(root, "init", "-q")
    monkeypatch.chdir(root)
    return root


def key(node: dict[str, Any]) -> str:
    return json.dumps(node, sort_keys=True)


def assert_fixture_nodes(nodes: list[dict[str, Any]]) -> None:
    assert sorted(map(key, nodes)) == sorted(map(key, EXPECTED))
    text = json.dumps(nodes)
    for name in LEAKED:
        assert name not in text


# The shared fixture ----------------------------------------------------------------------


def test_shared_fixture_publishes_the_expected_nodes(fixture: Path) -> None:
    document = fw.structure().document
    assert_fixture_nodes(document["nodes"])
    assert document["schema"] == "framework.artifact/v1"
    assert document["kind"] == "tree"
    assert document["source"] == {"language": "python"}
    assert document["root"] == "my-project"
    assert document["base"] == ""
    assert document["method"] == "git"
    assert "open" not in document and "highlight" not in document


def test_without_git_the_root_gitignore_decides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "my-project"
    root.mkdir()
    write_fixture(root)
    monkeypatch.chdir(root)
    monkeypatch.setattr(st, "in_work_tree", lambda path: False)
    document = fw.structure().document
    assert document["method"] == "gitignore"
    assert_fixture_nodes(document["nodes"])


def test_with_no_gitignore_nothing_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "my-project"
    root.mkdir()
    write_fixture(root)
    (root / ".gitignore").unlink()
    monkeypatch.chdir(root)
    monkeypatch.setattr(st, "in_work_tree", lambda path: False)
    document = fw.structure().document
    assert document["method"] == "none"
    paths = {n["path"] for n in document["nodes"]}
    assert {"data/raw/a.csv", "outputs/fit.rds", ".env"} <= paths
    # Private folders are stubs whatever the ignore rules say
    assert {"path": "data/private", "type": "dir", "private": True, "hidden": 1} in (
        document["nodes"]
    )
    assert "ids.csv" not in json.dumps(document)


# Printed trees ---------------------------------------------------------------------------

LOCAL = """\
my-project/
├── data/
│   ├── private/        private · 1 file
│   └── raw/            ignored by data/raw/ (.gitignore:1) · 2 files
├── outputs/
│   ├── table.csv
│   ├── draws.rds       ignored by *.rds (.gitignore:2)
│   └── fit.rds         ignored by *.rds (.gitignore:2)
├── R/                  Reusable functions
│   └── clean.R
├── .gitignore
├── README.md           Start here
└── .env                ignored by .env (.gitignore:3)"""

PUBLISHED = """\
my-project/
├── data/
│   ├── private/        private · 1 file
│   └── raw/            ignored by data/raw/ (.gitignore:1) · 2 files
├── outputs/
│   ├── table.csv
│   └── *.rds           ignored by *.rds (.gitignore:2) · 2 files
├── R/                  Reusable functions
│   └── clean.R
├── .gitignore
├── README.md           Start here
└── .env                ignored by .env (.gitignore:3) · 1 file"""

NOTES = {"R/": "Reusable functions", "README.md": "Start here"}


def test_the_local_tree_lists_ignored_files_by_name(fixture: Path) -> None:
    tree = fw.structure(notes=NOTES)
    assert str(tree) == LOCAL
    assert repr(tree) == LOCAL


def test_the_published_tree_groups_ignored_files(fixture: Path) -> None:
    assert fw.structure(notes=NOTES).published == PUBLISHED


def test_the_local_tree_lists_twenty_ignored_files_per_rule(fixture: Path) -> None:
    for i in range(23):
        (fixture / "outputs" / f"m{i:02}.rds").write_text("x", encoding="utf-8")
    lines = str(fw.structure("outputs")).splitlines()
    assert sum("ignored by *.rds" in line for line in lines) == 21
    assert lines[-1] == "└── … 5 more            ignored by *.rds (.gitignore:2)"


def test_counts_read_as_files() -> None:
    assert st._count_text(1) == "1 file"
    assert st._count_text(1520) == "1,520 files"
    assert st._count_text(10_000) == "10,000+ files"


def test_the_annotation_column_widens_for_long_names(fixture: Path) -> None:
    (fixture / "R" / "a_rather_long_file_name.R").write_text("x", encoding="utf-8")
    text = str(fw.structure("R", notes={"a_rather_long_file_name.R": "Long"}))
    assert text.splitlines()[1] == "├── a_rather_long_file_name.R  Long"


# Arguments -------------------------------------------------------------------------------


def test_depth_makes_deeper_folders_stubs(fixture: Path) -> None:
    (fixture / "R" / "sub").mkdir()
    (fixture / "R" / "sub" / "x.R").write_text("x", encoding="utf-8")
    nodes = fw.structure(depth=1).document["nodes"]
    assert {"path": "R", "type": "dir", "depth": True, "hidden": 2} in nodes
    assert {"path": "outputs", "type": "dir", "depth": True, "hidden": 3} in nodes
    assert not any(n["path"].startswith("R/") for n in nodes)
    nodes = fw.structure(depth=2).document["nodes"]
    assert {"path": "R", "type": "dir"} in nodes
    assert {"path": "R/sub", "type": "dir", "depth": True, "hidden": 1} in nodes
    assert "R/          1 file" not in str(fw.structure(depth=1))
    assert "├── R/                  2 files" in str(fw.structure(depth=1))


def test_depth_must_be_a_positive_whole_number(fixture: Path) -> None:
    for depth in (0, -1, 1.5, True):
        with pytest.raises(FrameworkError, match="depth"):
            fw.structure(depth=depth)  # type: ignore[arg-type]


def test_exclude_leaves_paths_out_with_no_stub(fixture: Path) -> None:
    nodes = fw.structure(exclude=["outputs", "*.R"]).document["nodes"]
    paths = {n["path"] for n in nodes}
    assert not any(p.startswith("outputs") for p in paths)
    assert "R/clean.R" not in paths
    assert "R" in paths
    nodes = fw.structure(exclude="data/raw/").document["nodes"]
    assert not any(n["path"] == "data/raw" for n in nodes)


def test_ignored_hide_leaves_ignored_paths_out(fixture: Path) -> None:
    nodes = fw.structure(ignored="hide").document["nodes"]
    assert not any("ignored" in n for n in nodes)
    assert {"path": "data/private", "type": "dir", "private": True, "hidden": 1} in nodes
    assert "fit.rds" not in str(fw.structure(ignored="hide"))
    with pytest.raises(FrameworkError, match="rules"):
        fw.structure(ignored="show")


def test_an_ignored_private_folder_is_hidden_with_the_ignored(fixture: Path) -> None:
    with (fixture / ".gitignore").open("a", encoding="utf-8") as handle:
        handle.write("data/private/\n")
    stub = {
        "path": "data/private",
        "type": "dir",
        "ignored": {"rule": "data/private/", "source": ".gitignore", "line": 4},
        "private": True,
        "hidden": 1,
    }
    assert stub in fw.structure().document["nodes"]
    assert "data/private" not in {n["path"] for n in fw.structure(ignored="hide").nodes}


def test_notes_open_and_highlight(fixture: Path) -> None:
    document = fw.structure(
        notes={"R/": "Reusable functions", "./README.md": "Start here"},
        open=["R/", "data"],
        highlight="README.md",
    ).document
    assert {"path": "R", "type": "dir", "note": "Reusable functions"} in document["nodes"]
    assert {"path": "README.md", "type": "file", "note": "Start here"} in document["nodes"]
    assert document["open"] == ["R", "data"]
    assert document["highlight"] == ["README.md"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"notes": {"missing.R": "x"}}, 'notes names "missing.R", which is not in the tree'),
        ({"notes": {"data/raw/a.csv": "x"}}, "not in the tree"),
        ({"notes": {"outputs/fit.rds": "x"}}, "not in the tree"),
        ({"notes": {"R": "x" * 501}}, "over 500 characters"),
        ({"notes": ["R"]}, "notes must be a dict"),
        ({"open": "nope"}, 'open names "nope", which is not in the tree'),
        ({"open": "README.md"}, "not a folder"),
        ({"highlight": ["R", "data/private/ids.csv"]}, "highlight names"),
    ],
)
def test_paths_not_in_the_tree_are_errors(
    fixture: Path, arguments: dict[str, Any], message: str
) -> None:
    with pytest.raises(FrameworkError, match=message):
        fw.structure(**arguments)


def test_a_subfolder_is_its_own_root_with_a_base(fixture: Path) -> None:
    document = fw.structure("outputs").document
    assert document["root"] == "outputs"
    assert document["base"] == "outputs"
    assert sorted(map(key, document["nodes"])) == sorted(
        map(
            key,
            [
                {"path": "table.csv", "type": "file"},
                {
                    "path": "",
                    "type": "ignored",
                    "ignored": {"rule": "*.rds", "source": ".gitignore", "line": 2},
                    "hidden": 2,
                },
            ],
        )
    )


def test_several_folders_publish_as_one_tree(fixture: Path) -> None:
    (fixture / "R" / "sub").mkdir()
    (fixture / "R" / "sub" / "x.R").write_text("x", encoding="utf-8")
    tree = fw.structure(["R/sub", "outputs", "data/raw"])
    document = tree.document
    assert document["root"] == "my-project"
    assert document["base"] == ""
    nodes = document["nodes"]
    assert {"path": "R/sub", "type": "dir"} in nodes
    assert {"path": "R/sub/x.R", "type": "file"} in nodes
    assert {"path": "outputs/table.csv", "type": "file"} in nodes
    assert {
        "path": "data/raw",
        "type": "dir",
        "ignored": {"rule": "data/raw/", "source": ".gitignore", "line": 1},
        "hidden": 2,
    } in nodes
    assert not any(n["path"] in ("R/clean.R", "README.md", "data/private") for n in nodes)
    assert "a.csv" not in json.dumps(document)
    assert tree.published.splitlines()[:4] == [
        "my-project/",
        "├── data/",
        "│   └── raw/            ignored by data/raw/ (.gitignore:1) · 2 files",
        "├── outputs/",
    ]


def test_an_ignored_or_private_folder_alone_is_refused(fixture: Path) -> None:
    with pytest.raises(FrameworkError, match="ignored by data/raw/"):
        fw.structure("data/raw")
    with pytest.raises(FrameworkError, match="private folder"):
        fw.structure("data/private")
    (fixture / "data" / "private" / "sub").mkdir()
    with pytest.raises(FrameworkError, match="inside a private folder"):
        fw.structure("data/private/sub")
    with pytest.raises(FrameworkError, match="not a folder"):
        fw.structure("README.md")
    with pytest.raises(FrameworkError, match="outside the project"):
        fw.structure("..")


def test_git_is_left_out_and_links_are_files(fixture: Path) -> None:
    (fixture / "linked").symlink_to(fixture / "R", target_is_directory=True)
    nodes = fw.structure().document["nodes"]
    assert not any(n["path"].startswith(".git/") or n["path"] == ".git" for n in nodes)
    assert {"path": "linked", "type": "file"} in nodes
    assert not any(n["path"].startswith("linked/") for n in nodes)


def test_rules_from_outside_the_root_never_name_their_file(
    fixture: Path, plain_git: Path, tmp_path: Path
) -> None:
    excludes = tmp_path / "global-excludes"
    excludes.write_text("*.log\n", encoding="utf-8")
    plain_git.write_text(f"[core]\n\texcludesFile = {excludes.as_posix()}\n", encoding="utf-8")
    (fixture / ".git" / "info" / "exclude").write_text("*.tmp\n", encoding="utf-8")
    (fixture / "run.log").write_text("x", encoding="utf-8")
    (fixture / "scratch.tmp").write_text("x", encoding="utf-8")
    nodes = fw.structure().document["nodes"]
    sources = {n["ignored"]["rule"]: n["ignored"]["source"] for n in nodes if "ignored" in n}
    assert sources["*.log"] == "global"
    assert sources["*.tmp"] == "info/exclude"
    assert str(tmp_path) not in json.dumps(nodes)


def test_a_nested_gitignore_names_its_path(fixture: Path) -> None:
    (fixture / "outputs" / ".gitignore").write_text("*.csv\n", encoding="utf-8")
    nodes = fw.structure().document["nodes"]
    assert {
        "path": "outputs",
        "type": "ignored",
        "ignored": {"rule": "*.csv", "source": "outputs/.gitignore", "line": 1},
        "hidden": 1,
    } in nodes


# The .gitignore matcher ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "path", "is_dir", "rule"),
    [
        ("*.rds\n", "outputs/fit.rds", False, "*.rds"),
        ("# comment\n\n*.rds\n!keep.rds\n", "outputs/keep.rds", False, None),
        ("*.rds\n!keep.rds\n", "outputs/fit.rds", False, "*.rds"),
        ("build/\n", "build", False, None),
        ("build/\n", "src/build", True, "build/"),
        ("/build\n", "src/build", True, None),
        ("/build\n", "build", True, "/build"),
        ("data/raw\n", "x/data/raw", True, None),
        ("data/raw\n", "data/raw", True, "data/raw"),
        ("**/cache\n", "a/b/cache", True, "**/cache"),
        ("logs/**\n", "logs/a/b.txt", False, "logs/**"),
        ("a/**/z\n", "a/z", False, "a/**/z"),
        ("a/**/z\n", "a/b/c/z", False, "a/**/z"),
        ("file?.txt\n", "file1.txt", False, "file?.txt"),
        ("file?.txt\n", "file10.txt", False, None),
        ("*.[co]\n", "x.o", False, "*.[co]"),
        ("*.txt   \n", "a.txt", False, "*.txt"),
        ("\\#hash\n", "#hash", False, "\\#hash"),
    ],
)
def test_the_gitignore_matcher(text: str, path: str, is_dir: bool, rule: str | None) -> None:
    found = st.gitignore_match(st.gitignore_patterns(text), path, is_dir)
    assert (found.text if found else None) == rule


def test_the_matcher_gives_line_numbers() -> None:
    found = st.gitignore_match(st.gitignore_patterns("# c\n\n*.rds\n"), "a.rds", False)
    assert found is not None and found.line == 3


# Publishing ------------------------------------------------------------------------------


@pytest.fixture
def documents(server: FakeCloud) -> FakeCloud:  # noqa: F811
    reply = {
        "id": 9,
        "url": "https://x/d/tree",
        "version": 2,
        "embed_link": "https://x/d/tree",
        "artifact_url": "https://x/artifacts/9",
    }
    server.route("POST", "/api/v1/documents", lambda r: httpx.Response(201, json=reply))
    return server


def uploaded(cloud: FakeCloud, name: str) -> dict[str, Any]:
    body = cloud.requests[-1].content.decode("utf-8")
    part = body.split(f'filename="{name}"', 1)[1].split("\r\n\r\n", 1)[1]
    document: dict[str, Any] = json.loads(part.rsplit("\r\n--", 1)[0])
    return document


@needs_git
def test_publish_structure_uploads_the_tree(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_fixture(cloud_project, env=False)
    (cloud_project / "inputs").rmdir()
    git(cloud_project, "init", "-q")
    caplog.set_level("INFO", logger="framework")
    published = fw.publish_structure(
        notes=NOTES, highlight="README.md", to="cloud", display={"ignored": False}
    )
    assert published == "https://x/d/tree"
    assert published.artifact_url == "https://x/artifacts/9"  # type: ignore[union-attr]
    body = documents.requests[-1].content
    assert b'name="kind"\r\n\r\ntree' in body
    assert b'name="slug"\r\n\r\nstructure' in body
    assert b'name="title"\r\n\r\nproject structure' in body
    assert b'name="display"\r\n\r\n{"ignored":false}' in body
    document = uploaded(documents, "structure.json")
    assert document["kind"] == "tree"
    assert document["highlight"] == ["README.md"]
    notes = {("R", "dir"): "Reusable functions", ("README.md", "file"): "Start here"}
    expected = [
        {**n, "note": notes[(n["path"], n["type"])]} if (n["path"], n["type"]) in notes else n
        for n in [*EXPECTED, {"path": "settings.yml", "type": "file"}]
    ]
    assert sorted(map(key, document["nodes"])) == sorted(map(key, expected))
    for name in LEAKED:
        assert name.encode() not in body
    messages = caplog.messages
    assert messages[0] == "\n".join(
        [
            "project/",
            *PUBLISHED.splitlines()[1:-1],
            "├── settings.yml",
            PUBLISHED.splitlines()[-1],
        ]
    )
    assert messages[1:] == [
        "Published version 2.",
        "Share:  https://x/d/tree",
        "Manage: https://x/artifacts/9",
    ]


@needs_git
def test_publish_structure_names_a_subfolder(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    write_fixture(cloud_project, env=False)
    git(cloud_project, "init", "-q")
    fw.publish_structure("outputs", to="cloud")
    body = documents.requests[-1].content
    assert b'name="slug"\r\n\r\nstructure-outputs' in body
    assert b'name="title"\r\n\r\noutputs structure' in body
    assert uploaded(documents, "structure-outputs.json")["base"] == "outputs"


@needs_git
def test_publish_structure_checks_before_sending(
    cloud_project: Path,  # noqa: F811
    documents: FakeCloud,
) -> None:
    write_fixture(cloud_project, env=False)
    with pytest.raises(FrameworkError, match="not in the tree"):
        fw.publish_structure(notes={"nope": "x"}, to="cloud")
    with pytest.raises(FrameworkError, match="Unknown display setting"):
        fw.publish_structure(to="cloud", display={"note": False})
    assert all(r.method == "GET" for r in documents.requests)
