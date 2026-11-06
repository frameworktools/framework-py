from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SHARED = Path(__file__).parent / "shared"


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Keep each test's environment variables and config folders separate.

    scaffold() loads .env files into os.environ, so the whole environment is
    restored after each test, along with what the loader remembers it set.
    """
    from framework import dotenv

    dotenv._loaded.clear()
    saved = dict(os.environ)
    home = tmp_path_factory.mktemp("home")
    for name in (
        "FW_CONFIG_ACTIVE",
        "R_CONFIG_ACTIVE",
        "R_CONFIG_NAME",
        "FW_CONFIG_HOME",
    ):
        os.environ.pop(name, None)
    for name in ("FW_CLOUD_URL", "FW_CLOUD_TOKEN", "FW_PROJECT_TOKEN", "FW_LEDGER", "FW_SEED"):
        os.environ.pop(name, None)
    os.environ["XDG_CONFIG_HOME"] = str(home / "xdg")
    os.environ["R_USER_CONFIG_DIR"] = str(home / "r-config")
    # Authoritative when set, so no test reads or writes the real keyring.
    os.environ["FW_TOKEN_FILE"] = str(home / "no-token")
    # Logout still clears the keyring, so turn it off (the macOS path honors this too).
    os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
        dotenv._loaded.clear()


@pytest.fixture
def r_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the project R built for the fixtures, as the current folder."""
    project = tmp_path / "r_project"
    shutil.copytree(FIXTURES / "r_project", project)
    monkeypatch.chdir(project)
    return project


@pytest.fixture(scope="session")
def r_expected() -> dict[str, Any]:
    """What R read from the fixture project (scripts/make_r_fixtures.R)."""
    data: dict[str, Any] = json.loads(
        (FIXTURES / "r_project.expected.json").read_text(encoding="utf-8")
    )
    return data


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project (just settings.yml) as the current folder."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "settings.yml").write_text("default:\n  project_name: Test\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def as_r_json(value: Any) -> Any:
    """A Python value in the shape jsonlite gives an R value.

    jsonlite writes a length-one vector as a scalar, and R cannot tell an
    empty mapping from an empty sequence.
    """
    if isinstance(value, dict):
        return {key: as_r_json(item) for key, item in value.items()} or []
    if isinstance(value, list):
        if len(value) == 1 and not isinstance(value[0], dict | list) and value[0] is not None:
            return value[0]
        return [as_r_json(item) for item in value]
    return value


def r_json(value: Any) -> Any:
    """Normalize R's JSON output the same way, for comparison."""
    if isinstance(value, dict):
        return {key: r_json(item) for key, item in value.items()} or []
    if isinstance(value, list):
        return [r_json(item) for item in value]
    return value


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any cloud request a test did not mock (see tests/test_cloud.py)."""
    import httpx

    from framework import cloud

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected network request: {request.method} {request.url}")

    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(refuse))
