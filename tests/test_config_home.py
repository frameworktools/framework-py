from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

import framework as fw
from framework.config_home import r_config_dir


def test_config_dir_order(tmp_path: Path) -> None:
    os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    assert fw.config_dir() == tmp_path / "xdg" / "framework"
    os.environ["FW_CONFIG_HOME"] = str(tmp_path / "custom")
    assert fw.config_dir() == tmp_path / "custom"
    del os.environ["FW_CONFIG_HOME"]
    del os.environ["XDG_CONFIG_HOME"]
    assert fw.config_dir() == Path.home() / ".config" / "framework"


def test_config_dir_is_not_created() -> None:
    assert not fw.config_dir().exists()


def test_r_config_dir(tmp_path: Path) -> None:
    os.environ["R_USER_CONFIG_DIR"] = str(tmp_path / "r")
    assert r_config_dir() == tmp_path / "r" / "R" / "framework"
    del os.environ["R_USER_CONFIG_DIR"]
    os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    assert r_config_dir() == tmp_path / "xdg" / "R" / "framework"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS default")
def test_r_config_dir_on_macos() -> None:
    del os.environ["XDG_CONFIG_HOME"]
    del os.environ["R_USER_CONFIG_DIR"]
    expected = Path.home() / "Library/Preferences/org.R-project.R/R/framework"
    assert r_config_dir() == expected


def make_r_folder(tmp_path: Path) -> Path:
    source = tmp_path / "r-config"
    source.mkdir()
    (source / "settings.yml").write_text("author: {name: R}\n", encoding="utf-8")
    (source / "cloud.yml").write_text("server: https://framework.pub\n", encoding="utf-8")
    (source / "cloud-token").write_text("fw_secret\n", encoding="utf-8")
    (source / "cloud-token-localhost_8080").write_text("fw_local\n", encoding="utf-8")
    (source / ".adopted-r").write_text("", encoding="utf-8")
    (source / "projects.yml").write_text("projects: []\n", encoding="utf-8")
    return source


def test_config_sync_copies_settings_and_tokens(tmp_path: Path) -> None:
    source = make_r_folder(tmp_path)
    result = fw.config_sync(source)
    target = fw.config_dir()
    assert result.copied == [
        "cloud-token",
        "cloud-token-localhost_8080",
        "cloud.yml",
        "settings.yml",
    ]
    assert sorted(p.name for p in target.iterdir()) == result.copied
    assert (target / "cloud-token").read_text(encoding="utf-8") == "fw_secret\n"
    assert (target / "settings.yml").stat().st_mtime == (source / "settings.yml").stat().st_mtime
    if sys.platform != "win32":
        assert stat.S_IMODE((target / "cloud-token").stat().st_mode) == 0o600


def test_config_sync_skips_newer_python_files(tmp_path: Path) -> None:
    source = make_r_folder(tmp_path)
    target = fw.config_dir()
    target.mkdir(parents=True)
    mine = target / "settings.yml"
    mine.write_text("author: {name: Python}\n", encoding="utf-8")
    later = (source / "settings.yml").stat().st_mtime + 60
    os.utime(mine, (later, later))

    result = fw.config_sync(source)
    assert "settings.yml" in result.skipped
    assert mine.read_text(encoding="utf-8") == "author: {name: Python}\n"


def test_config_sync_dry_run_and_missing_source(tmp_path: Path) -> None:
    source = make_r_folder(tmp_path)
    result = fw.config_sync(source, dry_run=True)
    assert result.copied and not fw.config_dir().exists()
    assert str(result).startswith("Would copy")

    empty = fw.config_sync(tmp_path / "nowhere")
    assert empty.copied == []
    assert "Nothing to copy" in str(empty)


def test_config_sync_defaults_to_r_folder(tmp_path: Path) -> None:
    os.environ["R_USER_CONFIG_DIR"] = str(tmp_path / "r")
    source = r_config_dir()
    source.mkdir(parents=True)
    (source / "cloud.yml").write_text("server: http://localhost:8080\n", encoding="utf-8")
    assert fw.config_sync().copied == ["cloud.yml"]


def test_config_sync_skips_cloud_sync_state(tmp_path: Path) -> None:
    source = make_r_folder(tmp_path)
    (source / "sync-state.yml").write_text("revision: 3\n", encoding="utf-8")
    (source / "cloud-settings.yml").write_text("author: {}\n", encoding="utf-8")
    copied = fw.config_sync(source).copied
    assert "sync-state.yml" not in copied
    assert "cloud-settings.yml" not in copied
