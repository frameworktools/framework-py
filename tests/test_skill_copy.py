import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

import framework as fw
import framework.skill_sync as skill_sync
from framework.errors import FrameworkError
from framework.skill_sync import _diverged, _executable_paths, assistant_folders, scan
from tests.test_skill_pull import SERVER, Cloud, cloud, cloud_entry  # noqa: F401


def make_skill(root: Path, folder: str, name: str, text: str, **extra: str) -> Path:
    path = root / folder / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\n---\n{text}\n", encoding="utf-8")
    for relative, content in extra.items():
        file = path / relative.replace("__", "/")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    return path


def statuses(result: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {(r["name"], r["to"]): r["status"] for r in result["skills"]}


def backups(root: Path) -> list[Path]:
    folder = root / ".framework/skills-backups"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def executables(path: Path) -> set[str]:
    return {f["path"] for f in scan(path) if f["executable"]}


# Folder names


def test_assistant_names_map_to_folders() -> None:
    folders = assistant_folders()
    assert folders["claude"] == ".claude/skills"
    assert folders["agents"] == folders["codex"] == folders["copilot"] == ".agents/skills"


def test_names_and_aliases_pick_the_folders(project: Path) -> None:
    make_skill(project, ".claude/skills", "figures", "mine")
    for alias in ("agents", "codex", "copilot"):
        result = fw.skills_copy(from_="claude", to=alias, project_path=project)
        assert result["from"] == ".claude/skills" and result["to"] == [".agents/skills"]
    result = fw.skills_copy(from_="codex", to="claude", project_path=project)
    assert result["from"] == ".agents/skills" and result["to"] == [".claude/skills"]


# Argument errors


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"from_": "claude", "to_dir": "x/skills"}, "not both"),
        ({"from_dir": ".claude/skills", "to": "agents"}, "not both"),
        ({"from_": "claude", "from_dir": ".claude/skills"}, "Give from_ or from_dir"),
        ({"from_": "claude", "to": "agents", "to_dir": "x"}, "Give to or to_dir"),
        ({"to": "agents"}, "Name the folder to copy from"),
        ({"from_": "gemini"}, 'Unknown assistant folder "gemini"'),
        ({"from_": "claude", "to": "claude"}, "same folder"),
        ({"from_dir": ".claude/skills", "to_dir": "./.claude//skills/"}, "same folder"),
        ({"from_dir": "nowhere/skills", "to_dir": "x"}, "nowhere/skills does not exist"),
        ({"from_": "claude", "skills": []}, "skills is empty"),
        (
            {"from_": "claude", "skills": ["nope", "figures", "gone", "nope"]},
            r"Not skills in \.claude/skills: nope, gone\.",
        ),
    ],
)
def test_bad_arguments_stop_with_a_clear_error(
    project: Path, kwargs: dict[str, Any], message: str
) -> None:
    make_skill(project, ".claude/skills", "figures", "mine")
    (project / ".agents/skills").mkdir(parents=True)
    with pytest.raises(FrameworkError, match=message):
        fw.skills_copy(project_path=project, **kwargs)
    assert not (project / ".agents/skills/figures").exists()


@pytest.mark.parametrize(
    "bad",
    [
        "/archive/skills",
        "D:skills",
        "D:/skills",
        "C:\\skills",
        "\\\\server\\share\\skills",
        "\\skills",
        "a/../skills",
        "a\\..\\skills",
        "..",
        "",
        ".",
        "skills/D:x",
        "a:b",
        "skills/notes.md:stream",
    ],
)
@pytest.mark.parametrize("side", ["from_dir", "to_dir"])
def test_paths_outside_the_project_are_refused_as_given(project: Path, bad: str, side: str) -> None:
    make_skill(project, ".claude/skills", "figures", "mine")
    kwargs = {"from_dir": ".claude/skills", "to_dir": "copy/skills", side: bad}
    with pytest.raises(FrameworkError, match=f"{side} must name a folder inside the project"):
        fw.skills_copy(project_path=project, **kwargs)
    assert not (project / "copy").exists() and not (project / "archive").exists()


def test_skill_paths_refuse_drive_and_absolute_spellings(project: Path) -> None:
    for bad in ("D:skills", "/archive/skills", "C:\\skills", "a\\..\\b", "folder/D:skills", "a:b"):
        with pytest.raises(ValueError, match="relative to the project"):
            skill_sync._path(project, bad)


@pytest.mark.parametrize(
    ("source", "target"),
    [("skills", "skills/foo/mirror"), ("skills/foo/mirror", "skills"), ("skills", "skills/x")],
)
def test_nested_folders_are_refused_either_way(project: Path, source: str, target: str) -> None:
    make_skill(project, source, "figures", "mine")
    with pytest.raises(FrameworkError, match="overlap"):
        fw.skills_copy(from_dir=source, to_dir=target, project_path=project)


def test_symlinked_spellings_of_the_same_folder_are_refused(project: Path) -> None:
    make_skill(project, "src", "figures", "mine")
    try:
        (project / "alias").symlink_to(project / "src", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    # alias is itself a symlink, so it is refused before identity is compared
    with pytest.raises(FrameworkError):
        fw.skills_copy(from_dir="src", to_dir="alias", project_path=project)


def test_case_insensitive_volumes_compare_by_identity(project: Path) -> None:
    make_skill(project, "src", "figures", "mine")
    if not (project / "SRC").exists():
        pytest.skip("case-sensitive file system")
    with pytest.raises(FrameworkError, match="same folder"):
        fw.skills_copy(from_dir="src", to_dir="SRC", project_path=project)
    with pytest.raises(FrameworkError, match="overlap"):
        fw.skills_copy(from_dir="src", to_dir="SRC/mirror", project_path=project)


@pytest.mark.parametrize(
    "target",
    [
        ".framework",
        ".framework/skills-backups",
        ".framework/skills-backups/old",
        ".framework/skills.lock.json",
        ".framework/skills.sync.lock",
        ".framework/team-skills",
        ".FRAMEWORK/Skills-Backups",
        ".Framework/x",
    ],
)
@pytest.mark.parametrize("dry_run", [False, True])
def test_targets_in_the_framework_folder_are_refused(
    project: Path, target: str, dry_run: bool
) -> None:
    make_skill(project, ".claude/skills", "figures", "mine")
    with pytest.raises(FrameworkError, match=r"overlaps \.framework/"):
        fw.skills_copy(
            from_dir=".claude/skills", to_dir=target, dry_run=dry_run, project_path=project
        )
    assert not (project / ".framework/team-skills").exists()


def test_missing_source_is_reported_before_targets(project: Path) -> None:
    (project / "file").write_text("x\n")
    with pytest.raises(FrameworkError, match=r"\.claude/skills does not exist"):
        fw.skills_copy(from_dir=".claude/skills", to_dir=".framework", project_path=project)
    with pytest.raises(FrameworkError, match=r"\.claude/skills does not exist"):
        fw.skills_copy(from_dir=".claude/skills", to_dir="file/skills", project_path=project)


def test_a_real_run_takes_the_lock_before_looking_at_the_project(project: Path) -> None:
    make_skill(project, "src", "figures", "mine")
    (project / ".framework/skills.sync.lock").mkdir(parents=True)
    for kwargs in (
        {"from_": "claude"},  # source missing
        {"from_dir": "src"},  # no default targets
        {"from_dir": "src", "to_dir": "src/mirror"},  # overlap
    ):
        with pytest.raises(ValueError, match="Skill sync is locked"):
            fw.skills_copy(project_path=project, **kwargs)
    # Dry runs take no lock, so they report the problem itself
    with pytest.raises(FrameworkError, match="does not exist"):
        fw.skills_copy(from_="claude", dry_run=True, project_path=project)


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_target_root_that_is_not_a_folder_fails_in_preflight(
    project: Path, dry_run: bool
) -> None:
    make_skill(project, ".claude/skills", "figures", "mine")
    (project / "notes.txt").write_text("x\n")
    with pytest.raises(FrameworkError, match=r"notes\.txt: it is not a folder"):
        fw.skills_copy(
            from_dir=".claude/skills", to_dir="notes.txt", dry_run=dry_run, project_path=project
        )
    with pytest.raises(FrameworkError, match=r"notes\.txt/skills: it is not a folder"):
        fw.skills_copy(
            from_dir=".claude/skills",
            to_dir="notes.txt/skills",
            dry_run=dry_run,
            project_path=project,
        )
    # A default target that is a file fails too
    (project / ".agents").mkdir()
    (project / ".agents/skills").write_text("x\n")
    with pytest.raises(FrameworkError, match=r"\.agents/skills: it is not a folder"):
        fw.skills_copy(from_="claude", dry_run=dry_run, project_path=project)
    assert (project / "notes.txt").read_text() == "x\n"


# Targets


def test_default_target_is_every_other_existing_assistant_folder(project: Path) -> None:
    make_skill(project, ".claude/skills", "figures", "mine")
    with pytest.raises(FrameworkError, match="Nothing to copy to"):
        fw.skills_copy(from_="claude", project_path=project)
    (project / ".agents/skills").mkdir(parents=True)
    result = fw.skills_copy(from_="claude", project_path=project)
    assert result["to"] == [".agents/skills"]
    assert (project / ".agents/skills/figures/SKILL.md").read_text() == (
        project / ".claude/skills/figures/SKILL.md"
    ).read_text()


def test_default_targets_are_sorted_by_path(project: Path) -> None:
    make_skill(project, "team/skills", "figures", "mine")
    (project / ".claude/skills").mkdir(parents=True)
    (project / ".agents/skills").mkdir(parents=True)
    result = fw.skills_copy(from_dir="team/skills", project_path=project)
    assert result["to"] == [".agents/skills", ".claude/skills"]


def test_folders_by_path(project: Path) -> None:
    make_skill(project, ".claude/skills", "figures", "mine")
    result = fw.skills_copy(
        from_dir=".claude/skills", to_dir="some/other/skills", project_path=project
    )
    assert statuses(result) == {("figures", "some/other/skills/figures"): "copied"}
    assert (project / "some/other/skills/figures/SKILL.md").is_file()


# Discovery


def test_only_folders_with_a_skill_md_are_skills(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    (project / ".claude/skills/templates").mkdir()
    (project / ".claude/skills/templates/notes.txt").write_text("not a skill\n")
    (project / ".claude/skills/README.md").write_text("a file\n")
    make_skill(project, ".agents/skills", "alpha", "old")
    (project / ".agents/skills/drafts").mkdir()
    result = fw.skills_copy(from_="claude", project_path=project)
    assert statuses(result) == {("alpha", ".agents/skills/alpha"): "copied"}
    assert not (project / ".agents/skills/templates").exists()
    with pytest.raises(FrameworkError, match=r"Not skills in \.claude/skills: templates"):
        fw.skills_copy(from_="claude", skills=["templates"], project_path=project)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlinked_skills_and_companions_are_errors(project: Path) -> None:
    real = make_skill(project, "elsewhere", "linked", "x")
    make_skill(project, ".claude/skills", "alpha", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    (project / ".claude/skills/linked").symlink_to(real, target_is_directory=True)
    with pytest.raises(FrameworkError, match=r"symlinks are unsupported: \.claude/skills/linked"):
        fw.skills_copy(from_="claude", project_path=project)
    (project / ".claude/skills/linked").unlink()

    beta = project / ".claude/skills/beta"
    beta.mkdir()
    (beta / "SKILL.md").symlink_to(real / "SKILL.md")
    with pytest.raises(FrameworkError, match=r"beta/SKILL\.md"):
        fw.skills_copy(from_="claude", project_path=project)
    (beta / "SKILL.md").unlink()
    beta.rmdir()

    (project / ".claude/skills/alpha/extra.md").symlink_to(real / "SKILL.md")
    before = tree(project / ".agents")
    with pytest.raises(ValueError, match="symlinks are unsupported"):
        fw.skills_copy(from_="claude", project_path=project)
    assert tree(project / ".agents") == before


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root"
)
def test_unreadable_source_folders_are_errors_and_leave_targets_intact(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    source = make_skill(project, ".claude/skills", "beta", "new", **{"refs__a.md": "a\n"})
    make_skill(project, ".agents/skills", "alpha", "old")
    make_skill(project, ".agents/skills", "beta", "old")
    before = tree(project / ".agents")
    (source / "refs").chmod(0)
    try:
        with pytest.raises(PermissionError):
            fw.skills_copy(from_="claude", project_path=project)
    finally:
        (source / "refs").chmod(0o755)
    assert tree(project / ".agents") == before
    assert backups(project) == []


def test_a_target_folder_that_is_not_a_skill_stops_before_any_write(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    make_skill(project, ".claude/skills", "beta", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    (project / ".agents/skills/beta").mkdir()
    (project / ".agents/skills/beta/notes.txt").write_text("mine\n")
    before = tree(project / ".agents")
    with pytest.raises(FrameworkError, match="beta exists but is not a skill folder"):
        fw.skills_copy(from_="claude", project_path=project)
    assert tree(project / ".agents") == before


# Filter


def test_skills_filter_copies_only_the_named_skills_in_order(project: Path) -> None:
    make_skill(project, ".claude/skills", "figures", "mine")
    make_skill(project, ".claude/skills", "tables", "mine")
    make_skill(project, ".claude/skills", "zeta", "mine")
    make_skill(project, ".agents/skills", "only-here", "theirs")
    result = fw.skills_copy(
        from_="claude", skills=["zeta", "figures", "zeta"], project_path=project
    )
    # Only the named skills are reported, so nothing is listed as missing
    assert [r["name"] for r in result["skills"]] == ["zeta", "figures"]
    assert not (project / ".agents/skills/tables").exists()


# Copying


def test_copies_skill_md_and_companion_files_with_modes(project: Path) -> None:
    source = make_skill(
        project,
        ".claude/skills",
        "figures",
        "new",
        **{"references__theme.md": "theme\n", "scripts__render.sh": "#!/bin/sh\n"},
    )
    (source / "scripts/render.sh").chmod(0o755)
    (source / ".env").write_text("SECRET=1\n")
    target = make_skill(project, ".agents/skills", "figures", "old", **{"stale.md": "old\n"})
    (target / ".env").write_text("TARGET=1\n")
    result = fw.skills_copy(from_="claude", project_path=project)
    assert (target / "SKILL.md").read_text() == (source / "SKILL.md").read_text()
    assert (target / "references/theme.md").read_text() == "theme\n"
    if sys.platform != "win32":
        assert executables(target) == {"scripts/render.sh"}
    # Excluded files are not copied and stay in the target
    assert (target / ".env").read_text() == "TARGET=1\n"
    # Other target-only files leave the active copy and stay in the backup
    assert not (target / "stale.md").exists()
    backup = project / result["skills"][0]["backup"]
    assert (backup / "stale.md").read_text() == "old\n"


def test_windows_mode_sidecar_with_one_executable(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(skill_sync, "_windows", lambda: True)
    source = make_skill(project, ".claude/skills", "figures", "new", **{"run.sh": "#!/bin/sh\n"})
    # A single executable, as a scalar string (older R) and as an array
    for written in ("run.sh", ["run.sh"]):
        (source / ".framework-skill.json").write_text(json.dumps({"executable_paths": written}))
        assert _executable_paths(source / ".framework-skill.json") == {"run.sh"}
        assert executables(source) == {"run.sh"}
    make_skill(project, ".agents/skills", "figures", "old")
    fw.skills_copy(from_="claude", project_path=project)
    target = project / ".agents/skills/figures"
    sidecar = json.loads((target / ".framework-skill.json").read_text())
    assert sidecar == {"executable_paths": ["run.sh"]}
    assert executables(target) == {"run.sh"}


def test_invalid_mode_sidecar_is_an_error(project: Path) -> None:
    sidecar = project / ".framework-skill.json"
    sidecar.write_text(json.dumps({"executable_paths": 3}))
    with pytest.raises(ValueError, match="Invalid executable_paths"):
        _executable_paths(sidecar)


def test_backs_up_replaced_copies_and_skips_identical_ones(project: Path, capsys: Any) -> None:
    make_skill(project, ".claude/skills", "figures", "new")
    make_skill(project, ".claude/skills", "tables", "same")
    make_skill(project, ".agents/skills", "figures", "old")
    make_skill(project, ".agents/skills", "tables", "same")
    make_skill(project, ".agents/skills", "only-here", "theirs")
    result = fw.skills_copy(from_="claude", project_path=project)
    assert statuses(result) == {
        ("figures", ".agents/skills/figures"): "copied",
        ("tables", ".agents/skills/tables"): "current",
        ("only-here", ".agents/skills/only-here"): "missing",
    }
    assert result["counts"] == {"copied": 1, "current": 1, "missing": 1}
    assert result["message"] == (
        "Skills from .claude/skills: 1 copied, 1 up to date, 1 not in .claude/skills (kept)"
    )
    saved = backups(project)
    assert len(saved) == 1 and "old" in (saved[0] / "SKILL.md").read_text()
    copied = next(r for r in result["skills"] if r["status"] == "copied")
    assert copied["backup"] == saved[0].relative_to(project).as_posix()
    assert all(r["backup"] is None for r in result["skills"] if r["status"] != "copied")
    assert "skills-backups/" in (project / ".framework/.gitignore").read_text().splitlines()
    err = capsys.readouterr().err
    assert "  Copied: .claude/skills/figures -> .agents/skills/figures\n" in err
    assert f"    Backup: {copied['backup']}\n" in err
    assert "tables" not in err
    # Again: everything is current, and nothing more is backed up
    again = fw.skills_copy(from_="claude", project_path=project)
    assert again["counts"]["copied"] == 0
    assert len(backups(project)) == 1


def test_a_new_copy_has_no_backup(project: Path, capsys: Any) -> None:
    make_skill(project, ".claude/skills", "figures", "new")
    (project / ".agents/skills").mkdir(parents=True)
    result = fw.skills_copy(from_="claude", project_path=project)
    assert result["skills"][0]["backup"] is None
    assert "Backup:" not in capsys.readouterr().err


def test_never_deletes_skills_missing_from_the_source(project: Path, capsys: Any) -> None:
    make_skill(project, ".claude/skills", "figures", "mine")
    make_skill(project, ".agents/skills", "only-here", "theirs")
    result = fw.skills_copy(from_="claude", project_path=project)
    missing = next(r for r in result["skills"] if r["status"] == "missing")
    assert missing == {
        "name": "only-here",
        "status": "missing",
        "from": None,
        "to": ".agents/skills/only-here",
        "backup": None,
        "paths": [".agents/skills/only-here"],
        "reason_code": None,
        "hint": None,
    }
    assert (project / ".agents/skills/only-here/SKILL.md").is_file()
    assert "  Not in .claude/skills (kept): .agents/skills/only-here" in capsys.readouterr().err


def test_leaves_the_skill_lock_alone(project: Path) -> None:
    lock = project / ".framework/skills.lock.json"
    lock.parent.mkdir()
    lock.write_text('{"schema": 1, "skills": {}}\n')
    make_skill(project, ".claude/skills", "figures", "new")
    make_skill(project, ".agents/skills", "figures", "old")
    fw.skills_copy(from_="claude", project_path=project)
    assert lock.read_text() == '{"schema": 1, "skills": {}}\n'
    assert not (project / ".framework/skills.sync.lock").exists()


def test_an_active_sync_lock_stops_a_copy_but_not_a_dry_run(project: Path) -> None:
    make_skill(project, ".claude/skills", "figures", "new")
    make_skill(project, ".agents/skills", "figures", "old")
    (project / ".framework/skills.sync.lock").mkdir(parents=True)
    before = tree(project)
    with pytest.raises(ValueError, match="Skill sync is locked"):
        fw.skills_copy(from_="claude", project_path=project)
    assert tree(project) == before
    assert fw.skills_copy(from_="claude", dry_run=True, project_path=project)["counts"] == {
        "copied": 1,
        "current": 0,
        "missing": 0,
    }
    assert (project / ".framework/skills.sync.lock").is_dir()


def test_dry_run_writes_nothing(project: Path, capsys: Any) -> None:
    make_skill(project, ".claude/skills", "figures", "new")
    make_skill(project, ".claude/skills", "tables", "new")
    make_skill(project, ".agents/skills", "figures", "old")
    before = sorted(p.relative_to(project) for p in project.rglob("*"))
    result = fw.skills_copy(from_="claude", dry_run=True, project_path=project)
    assert result["dry_run"] is True
    assert result["counts"]["copied"] == 2
    assert result["message"] == "Skills (dry run, nothing written) from .claude/skills: 2 to copy"
    assert all(r["backup"] is None for r in result["skills"])
    assert sorted(p.relative_to(project) for p in project.rglob("*")) == before
    err = capsys.readouterr().err
    assert "  Would copy: .claude/skills/figures -> .agents/skills/figures" in err
    assert "Backup:" not in err


# Partial failure


def failing_install(monkeypatch: pytest.MonkeyPatch, after: int, hook: Any = None) -> None:
    real = skill_sync._install
    calls: list[Any] = []

    def install(*args: Any, **kwargs: Any) -> Any:
        if len(calls) == after:
            if hook:
                hook(calls)
            raise OSError("disk full")
        result = real(*args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(skill_sync, "_install", install)


def test_a_failed_copy_undoes_the_copies_before_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    make_skill(project, ".claude/skills", "beta", "new")
    make_skill(project, ".claude/skills", "gamma", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    make_skill(project, ".agents/skills", "beta", "old")
    before = tree(project / ".agents")
    failing_install(monkeypatch, after=2)
    with pytest.raises(OSError, match="disk full") as caught:
        fw.skills_copy(from_="claude", project_path=project)
    assert "".join(caught.value.__notes__) == (
        "skills_copy() failed copying .claude/skills/gamma -> .agents/skills/gamma; "
        "the 2 copies made before it were undone."
    )
    assert tree(project / ".agents") == before
    assert backups(project) == []
    assert not (project / ".framework/skills.sync.lock").exists()


def test_a_failure_that_cannot_be_undone_names_the_replaced_copies(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    make_skill(project, ".claude/skills", "alpha", "new")
    make_skill(project, ".claude/skills", "beta", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    make_skill(project, ".agents/skills", "beta", "old")

    def lose_backup(calls: list[Any]) -> None:
        backup = calls[0][".agents/skills/alpha"]
        shutil.rmtree(project / backup)

    failing_install(monkeypatch, after=1, hook=lose_backup)
    with pytest.raises(FrameworkError, match="could not undo every change") as caught:
        fw.skills_copy(from_="claude", project_path=project)
    message = str(caught.value)
    assert "failed copying .claude/skills/beta -> .agents/skills/beta (disk full)" in message
    assert "Not restored: .agents/skills/alpha (old copy in .framework/skills-backups/" in message


def fail_renames_into(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Renames onto a folder called ``name`` fail: installing it and restoring it."""
    real = Path.rename

    def rename(self: Path, target: Any) -> Any:
        if Path(target).name == name:
            raise PermissionError(13, "Permission denied", str(target))
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)


def test_an_install_whose_restore_fails_names_the_target_and_backup(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    make_skill(project, ".claude/skills", "beta", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    make_skill(project, ".agents/skills", "beta", "old")
    fail_renames_into(monkeypatch, "beta")
    with pytest.raises(FrameworkError) as caught:
        fw.skills_copy(from_="claude", project_path=project)
    message = str(caught.value)
    assert "failed copying .claude/skills/beta -> .agents/skills/beta" in message
    assert "Not restored: .agents/skills/beta (old copy in .framework/skills-backups/" in message
    # The old copy is in the backup the error names, and alpha was put back
    backup = project / message.split("old copy in ")[1].split(")")[0]
    assert "old" in (backup / "SKILL.md").read_text()
    assert "old" in (project / ".agents/skills/alpha/SKILL.md").read_text()


def test_the_installer_reports_a_restore_it_could_not_do(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_skill(project, ".agents/skills", "beta", "old")
    target = project / ".agents/skills/beta"
    current = skill_sync.bundle_hash(scan(target))
    fail_renames_into(monkeypatch, "beta")
    with pytest.raises(skill_sync.SkillRestoreError) as caught:
        skill_sync._install(project, [".agents/skills/beta"], scan(target), current)
    [(where, backup)] = caught.value.stuck
    assert where == ".agents/skills/beta"
    assert "old" in (project / backup / "SKILL.md").read_text()
    assert isinstance(caught.value.__cause__, PermissionError)


# Divergence


def test_divergence_error_names_the_skill_and_the_call() -> None:
    message = _diverged([".claude/skills/x", ".agents/skills/x"], ["a", "b"])
    assert message == (
        ".claude/skills/x and .agents/skills/x differ. Pick one and copy it over the other: "
        'skills_copy(from_="claude", to="agents", skills=["x"])'
    )
    # Only one copy: it is the source, and the call names the missing side
    assert _diverged([".claude/skills/x", ".agents/skills/x"], ["a", None]) == (
        ".claude/skills/x exists and .agents/skills/x is missing. Copy it over: "
        'skills_copy(from_="claude", to="agents", skills=["x"])'
    )
    assert 'skills_copy(from_="agents", to="claude", skills=["x"])' in _diverged(
        [".claude/skills/x", ".agents/skills/x"], [None, "a"]
    )
    assert 'skills_copy(from_dir="team/skills", to_dir=".claude/skills", skills=["x"])' in (
        _diverged([".claude/skills/x", "team/skills/x"], [None, "a"])
    )
    custom = _diverged([".claude/skills/x", "team/skills/x"], ["a", "b"])
    assert 'skills_copy(from_dir=".claude/skills", to_dir="team/skills", skills=["x"])' in custom


def test_copying_resolves_diverged_copies_for_sync(
    cloud: Cloud,  # noqa: F811
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [claude, codex]}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")
    head = cloud_entry(1, "Version one")
    cloud.heads["team-id"] = {
        "skill_id": "team-id",
        "version": 1,
        "bundle": head["bundle"],
        "hash": head["sync"]["hash"],
    }
    fw.pull_skills(project)
    claude = project / ".claude/skills/team-style/SKILL.md"
    agents = project / ".agents/skills/team-style/SKILL.md"
    assert claude.read_text() == agents.read_text()
    lock = (project / ".framework/skills.lock.json").read_text()

    claude.write_text(claude.read_text() + "My edit\n")
    with pytest.raises(
        ValueError, match=r'skills_copy\(from_="claude", to="agents", skills=\["team-style"\]\)'
    ):
        fw.skills_sync(".claude/skills/team-style", project, direction="status")
    # push_skills() reports the diverged skill as failed and goes on
    failed = fw.push_skills(project, confirm=True)
    assert {s["name"]: s["status"] for s in failed["skills"]}["team-style"] == "failed"

    result = fw.skills_copy(from_="claude", skills=["team-style"], project_path=project)
    assert statuses(result) == {("team-style", ".agents/skills/team-style"): "copied"}
    assert agents.read_text() == claude.read_text()
    assert (project / ".framework/skills.lock.json").read_text() == lock
    status = fw.skills_sync(".claude/skills/team-style", project, direction="status")
    assert status["action"] == "push"
    pushed = fw.push_skills(project, confirm=True)
    assert [s["name"] for s in pushed["skills"] if s["status"] == "pushed"] == ["team-style"]


# Round 3: lookups, identity, modes, and Cloud


posix_user = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root"
)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_broken_symlink_in_a_skills_folder_is_an_error(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    (project / ".agents/skills").mkdir(parents=True)
    (project / ".claude/skills/gone").symlink_to(project / "nowhere")
    with pytest.raises(FrameworkError, match=r"Broken symlink in a skills folder: .*gone"):
        fw.skills_copy(from_="claude", dry_run=True, project_path=project)
    (project / ".claude/skills/gone").unlink()
    (project / ".agents/skills/gone").symlink_to(project / "nowhere")
    with pytest.raises(FrameworkError, match=r"Broken symlink in a skills folder: .*gone"):
        fw.skills_copy(from_="claude", project_path=project)
    assert not (project / ".agents/skills/alpha").exists()


def test_dot_folders_are_skipped(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    make_skill(project, ".claude/skills", ".skill-stage-0123", "leftover")
    make_skill(project, ".agents/skills", ".skill-stage-4567", "leftover")
    result = fw.skills_copy(from_="claude", project_path=project)
    assert statuses(result) == {("alpha", ".agents/skills/alpha"): "copied"}
    assert not (project / ".agents/skills/.skill-stage-0123").exists()


@posix_user
def test_permission_failures_in_default_target_discovery_are_errors(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    (project / ".agents/skills").mkdir(parents=True)
    (project / ".agents").chmod(0)
    try:
        with pytest.raises(PermissionError):
            fw.skills_copy(from_="claude", dry_run=True, project_path=project)
    finally:
        (project / ".agents").chmod(0o755)


@posix_user
def test_permission_failures_looking_up_the_source_are_errors(project: Path) -> None:
    make_skill(project, "team/skills", "alpha", "new")
    (project / "team").chmod(0)
    try:
        with pytest.raises(PermissionError):
            fw.skills_copy(from_dir="team/skills", to_dir="copy", project_path=project)
    finally:
        (project / "team").chmod(0o755)
    assert not (project / "copy").exists()


def test_case_variants_are_one_folder_and_one_row(project: Path) -> None:
    make_skill(project, ".claude/skills", "Alpha", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    if not (project / ".agents/skills/ALPHA").exists():
        pytest.skip("case-sensitive file system")
    result = fw.skills_copy(from_="claude", project_path=project)
    assert [(r["name"], r["status"]) for r in result["skills"]] == [("Alpha", "copied")]
    assert "new" in (project / ".agents/skills/alpha/SKILL.md").read_text()
    again = fw.skills_copy(from_="claude", project_path=project)
    assert [(r["name"], r["status"]) for r in again["skills"]] == [("Alpha", "current")]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_rebuilt_folders_keep_their_permissions(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new", **{"refs__a.md": "new\n"})
    target = make_skill(project, ".agents/skills", "alpha", "old", **{"refs__a.md": "old\n"})
    (target / "refs").chmod(0o700)
    target.chmod(0o750)
    fw.skills_copy(from_="claude", project_path=project)
    assert (target / "refs/a.md").read_text() == "new\n"
    assert target.stat().st_mode & 0o777 == 0o750
    assert (target / "refs").stat().st_mode & 0o777 == 0o700
    # A new copy gets the usual mode, not a temporary folder's 0700
    umask = os.umask(0)
    os.umask(umask)
    make_skill(project, ".claude/skills", "beta", "new")
    fw.skills_copy(from_="claude", skills=["beta"], project_path=project)
    assert (project / ".agents/skills/beta").stat().st_mode & 0o777 == 0o777 & ~umask


def test_push_checks_every_copy_and_the_hint_restores_a_missing_one(
    cloud: Cloud,  # noqa: F811
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    (project / "settings.yml").write_text(
        "default:\n  project_type: project\n  ai: {assistants: [claude, codex]}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FW_CLOUD_TOKEN", "fw_user")
    head = cloud_entry(1, "Version one")
    cloud.heads["team-id"] = {
        "skill_id": "team-id",
        "version": 1,
        "bundle": head["bundle"],
        "hash": head["sync"]["hash"],
    }
    fw.pull_skills(project)
    # An edit to the second copy only is still seen
    agents = project / ".agents/skills/team-style/SKILL.md"
    agents.write_text(agents.read_text() + "Codex edit\n")
    pushed = fw.push_skills(project, confirm=True)
    assert {s["name"]: s["status"] for s in pushed["skills"]}["team-style"] == "failed"
    assert cloud.posts == []

    # A missing copy is never the suggested source
    fw.skills_copy(from_="agents", skills=["team-style"], project_path=project)
    shutil.rmtree(project / ".claude/skills/team-style")
    with pytest.raises(ValueError, match="is missing") as caught:
        fw.skills_sync(".agents/skills/team-style", project, direction="status")
    assert 'skills_copy(from_="agents", to="claude", skills=["team-style"])' in str(caught.value)
    fw.skills_copy(from_="agents", to="claude", skills=["team-style"], project_path=project)
    status = fw.skills_sync(".agents/skills/team-style", project, direction="status")
    assert status["action"] == "push"


# Round 4: modes, links, and restore errors


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_file_replacing_a_folder_gets_a_file_mode(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new", **{"assets": "now a file\n"})
    target = make_skill(project, ".agents/skills", "alpha", "old", **{"assets__a.png": "x\n"})
    (target / "assets").chmod(0o755)
    fw.skills_copy(from_="claude", project_path=project)
    assert (target / "assets").is_file()
    assert (target / "assets").stat().st_mode & 0o7777 == 0o644
    assert executables(target) == set()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks and POSIX permissions")
def test_restoring_modes_never_follows_links_in_excluded_folders(project: Path) -> None:
    elsewhere = project / "elsewhere"
    elsewhere.mkdir()
    elsewhere.chmod(0o711)
    make_skill(project, ".claude/skills", "alpha", "new")
    target = make_skill(project, ".agents/skills", "alpha", "old")
    (target / "node_modules").mkdir()
    (target / "node_modules/link").symlink_to(elsewhere, target_is_directory=True)
    fw.skills_copy(from_="claude", project_path=project)
    assert elsewhere.stat().st_mode & 0o7777 == 0o711
    assert (target / "node_modules/link").is_symlink()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_rebuilt_folders_keep_special_mode_bits(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new", **{"shared__a.md": "new\n"})
    target = make_skill(project, ".agents/skills", "alpha", "old", **{"shared__a.md": "old\n"})
    (target / "shared").chmod(0o1770)  # sticky: anyone may set it on their own folder
    target.chmod(0o2750)
    setgid_kept = target.stat().st_mode & 0o7777 == 0o2750
    fw.skills_copy(from_="claude", project_path=project)
    assert (target / "shared/a.md").read_text() == "new\n"
    assert (target / "shared").stat().st_mode & 0o7777 == 0o1770
    if setgid_kept:  # Some systems clear setgid for a group the user isn't in
        assert target.stat().st_mode & 0o7777 == 0o2750


def test_a_restore_failure_from_skills_copy_is_a_skill_restore_error(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    make_skill(project, ".claude/skills", "beta", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    make_skill(project, ".agents/skills", "beta", "old")
    fail_renames_into(monkeypatch, "beta")
    with pytest.raises(skill_sync.SkillRestoreError) as caught:
        fw.skills_copy(from_="claude", project_path=project)
    [(where, backup)] = caught.value.stuck
    assert where == ".agents/skills/beta"
    assert backup is not None and "old" in (project / backup / "SKILL.md").read_text()
    assert isinstance(caught.value, FrameworkError)


@pytest.mark.parametrize("source", [".framework", ".framework/skills-backups", ".FRAMEWORK/x"])
def test_sources_in_the_framework_folder_are_refused(project: Path, source: str) -> None:
    make_skill(project, ".framework/skills-backups", "old", "backup")
    make_skill(project, ".framework/x", "old", "backup")
    with pytest.raises(FrameworkError, match=r"Can't copy from .*overlaps \.framework/"):
        fw.skills_copy(from_dir=source, to_dir="restored", project_path=project)
    assert not (project / "restored").exists()


# Round 11: the lock is read before any write; joins roll back with the copy


def test_an_unreadable_lock_stops_the_copy_before_any_write(project: Path) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    (project / ".framework").mkdir()
    (project / ".framework/skills.lock.json").write_text('{"schema": 7, "skills": {}}')
    before = tree(project / ".agents")
    for dry_run in (True, False):
        with pytest.raises(ValueError, match="uses schema 7"):
            fw.skills_copy(from_="claude", dry_run=dry_run, project_path=project)
    assert tree(project / ".agents") == before
    assert backups(project) == []


def test_a_failed_join_undoes_the_copy(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_skill(project, ".claude/skills", "alpha", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    before = tree(project / ".agents")

    def broken(*args: Any) -> None:
        raise OSError("lock write failed")

    monkeypatch.setattr(skill_sync, "_save_changed", broken)
    with pytest.raises(OSError, match="lock write failed"):
        fw.skills_copy(from_="claude", project_path=project)
    assert tree(project / ".agents") == before


def test_an_existing_target_folder_keeps_its_on_disk_spelling(project: Path) -> None:
    make_skill(project, ".claude/skills", "Alpha", "new")
    make_skill(project, ".agents/skills", "alpha", "old")
    if not (project / ".agents/skills/ALPHA").exists():
        pytest.skip("case-sensitive file system")
    result = fw.skills_copy(from_="claude", project_path=project)
    assert [r["to"] for r in result["skills"]] == [".agents/skills/alpha"]
    assert sorted(p.name for p in (project / ".agents/skills").iterdir()) == ["alpha"]
