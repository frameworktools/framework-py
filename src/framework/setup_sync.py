"""A project's setup (its packages and skills), kept in step with Framework Cloud.

``pull_setup()`` brings Cloud's package list and skills into the project.
``push_setup()`` sends the project's package list, then its edited skills, back to Cloud.
Folders and structure are never part of the setup. R: ``pull_setup()``, ``push_setup()``.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from framework.errors import FrameworkError

_LOCK = ".framework/setup.lock.json"
# framework-py itself, and the packages Framework adds for a workflow's tools, are
# not part of the setup when unpinned: every project gets them.
_IMPLIED = {"framework-py", "ipykernel", "nbformat", "nbclient", "jupyterlab"}
_REQUIREMENT = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*"
    r"(?:@\s*(?P<url>\S+)|(?P<spec>[^;@]*))?\s*(;.*)?$"
)
_GITHUB = re.compile(r"^git\+https://github\.com/(?P<repo>[^@#\s]+?)(?:\.git)?(?:@(?P<ref>\S+))?$")


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _row(requirement: str) -> dict[str, Any] | None:
    """A pyproject.toml requirement as a setup package row, or None for one Cloud can't hold."""
    match = _REQUIREMENT.match(requirement)
    if match is None:
        return None
    row: dict[str, Any] = {
        "name": match["name"],
        "source": "pypi",
        "version": None,
        "auto_attach": False,
        "repo": None,
    }
    if match["url"]:
        github = _GITHUB.match(match["url"])
        if github is None:
            return None
        row.update(source="github", repo=github["repo"])
    elif match["spec"] and match["spec"].strip():
        row["version"] = re.sub(r"\s+", "", match["spec"])
    return row


def _version(row: dict[str, Any]) -> str | None:
    """The version as a requirement writes it: Cloud's bare "2.2" means ==2.2."""
    version = row.get("version")
    if not version:
        return None
    return str(version) if re.match(r"^[<>=!~]", str(version)) else f"=={version}"


def _same(local: dict[str, Any], cloud: dict[str, Any]) -> bool:
    if (local.get("source") or "pypi") != (cloud.get("source") or "pypi"):
        return False
    if local.get("source") == "github":
        return local.get("repo") == cloud.get("repo")
    return _version(local) == _version(cloud)


def _describe(row: dict[str, Any]) -> str:
    if row.get("source") == "github":
        return f"github {row.get('repo')}"
    return _version(row) or "any version"


def _local(root: Path, cloud: Collection[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """The project's packages from pyproject.toml, by normalized name, and their requirements.

    framework-py and the workflow tool packages count only when ``cloud``, the normalized
    names of the setup's packages, lists them too, or when they are pinned.
    """
    from framework.packages import _dependencies

    rows: dict[str, dict[str, Any]] = {}
    requirements: dict[str, str] = {}
    for requirement in _dependencies(root):
        row = _row(requirement)
        if row is None:
            continue
        key = _key(row["name"])
        implied = key in _IMPLIED and row["version"] is None and row["source"] == "pypi"
        if implied and key not in cloud:
            continue
        rows[key] = row
        requirements[key] = requirement
    return rows, requirements


def _other_language(language: str, function: str) -> FrameworkError | None:
    """An error for a project whose packages framework-py doesn't manage, such as an R one."""
    from framework.cloud import CloudError

    if language in ("python", "stata"):
        return None
    name = {"r": "an R"}.get(language, f"a {language}")
    package = "the framework R package's" if language == "r" else "its language package's"
    return CloudError(
        f"This is {name} project on Framework Cloud. Use {package} {function}() "
        f"or fw setup {function.split('_')[0]}. Nothing changed."
    )


def _lock_read(root: Path) -> dict[str, Any]:
    path = root / _LOCK
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _lock_write(root: Path, revision: str, server: str) -> None:
    path = root / _LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": 1,
        "revision": revision,
        "server": server,
        "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _connection(root: Path) -> tuple[str, str]:
    """The project's server and token, or CloudError when it has no token."""
    from framework.cloud import CloudError, cloud_server, project_token

    token = project_token(root)
    if token is None:
        raise CloudError("no project token in FW_PROJECT_TOKEN or the project's .env")
    return cloud_server(scope="project", project_dir=root).url, token


def _unreachable(error: Exception) -> FrameworkError:
    from framework.cloud import CloudError, _MissingEndpointError

    if isinstance(error, _MissingEndpointError):
        return CloudError("This Framework Cloud server has no setup endpoint yet. Nothing changed.")
    reason = str(error).rstrip(".") or type(error).__name__
    return CloudError(f"Framework Cloud not reached ({reason}). Nothing changed.")


@contextmanager
def _quiet() -> Iterator[None]:
    """Hold back Framework's messages, as when pull_skills() only plans."""
    from framework._log import logger

    level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(level)


def _ask(question: str, confirm: bool | None, function: str) -> bool:
    if confirm is not None:
        return confirm
    if not sys.stdin.isatty():
        raise FrameworkError(
            f"{function}() asks before changing anything. "
            "Without a console, pass confirm=True, or dry_run=True to only see the changes."
        )
    return input(f"{question} [y/n] ").strip().lower() in ("y", "yes")


def _project_name(root: Path) -> str:
    from framework.settings import settings_read

    try:
        name = settings_read(root / "settings.yml").get("project_name")
    except Exception:
        name = None
    return str(name) if name else root.resolve().name


def _skills_line(counts: dict[str, int]) -> str | None:
    labels = [
        ("added", "to add"),
        ("updated", "to update"),
        ("replaced", "to replace (backed up)"),
        ("edited", "locally edited (kept)"),
        ("removed", "no longer in the project's setup (kept)"),
    ]
    parts = [f"{counts.get(key, 0)} {label}" for key, label in labels if counts.get(key)]
    return f"  Skills: {', '.join(parts)}" if parts else None


def _install(root: Path, requirements: list[str]) -> None:
    """Add or update requirements in pyproject.toml and install them: uv, else pip."""
    from framework.packages import (
        _add_to_pyproject,
        _check,
        _pyproject,
        _refresh_agents_md,
        _run,
        _uv,
    )

    uv = _uv()
    if uv is not None:
        # uv add replaces the requirement of a package already listed. --raw keeps each
        # as Cloud has it: no added lower bound, and a GitHub URL stays in dependencies
        _check(_run([uv, "add", "--raw", *requirements], root), "uv add")
    else:
        path = _pyproject(root)
        for requirement in requirements:
            _replace_requirement(path, requirement)
            _add_to_pyproject(path, requirement)
        _check(_run([sys.executable, "-m", "pip", "install", *requirements], root), "pip install")
    _refresh_agents_md(root)


def _replace_requirement(path: Path, requirement: str) -> None:
    """Without uv: swap an existing requirement for the same package, keeping the layout."""
    from framework.packages import _dependencies, _package_name

    key = _key(_package_name(requirement))
    text = path.read_text(encoding="utf-8")
    for old in _dependencies(path.parent):
        if _key(_package_name(old)) == key and old != requirement:
            text = re.sub(
                r"([\"'])" + re.escape(old) + r"\1", json.dumps(requirement), text, count=1
            )
    path.write_text(text, encoding="utf-8")


def pull_setup(
    project_path: str | Path = ".",
    overwrite: bool = False,
    confirm: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Bring the project's packages and skills up to date with its setup on Framework Cloud.

    Reads the setup with the project token (``GET /api/v1/projects/setup``) and lists the
    changes first: packages to add, packages whose version or source differs, packages only
    in this project, and skills to add, update, or keep. Then asks "Apply these changes?".

    Applying adds the new packages to ``pyproject.toml`` and installs them (uv, else pip),
    then runs ``pull_skills()``. Local packages are never removed. A package whose local
    version or source differs keeps the local one unless ``overwrite=True``, which also
    replaces locally edited skills (backed up). The revision pulled is kept in
    ``.framework/setup.lock.json`` for ``push_setup()``.

    Args:
        project_path: The project's root directory. Default: the current directory.
        overwrite: Use Cloud's version of differing packages and replace edited skills.
        confirm: True applies without asking; False cancels. None asks in a console.
        dry_run: List the changes and write nothing.

    Returns:
        A dict: ``revision``, ``packages`` (``added``, ``changed``, ``differ_kept``, and
        ``kept_local``, as names), ``skills`` (the ``pull_skills()`` result, or None when
        nothing was applied), ``applied``, ``dry_run``, and ``message``.

    Raises:
        CloudError: Without a project token, offline, or when the server has no setup
            endpoint. Nothing changes then.
        FrameworkError: Without a console and without ``confirm=True`` or ``dry_run=True``.
    """
    from framework._log import inform, show_messages
    from framework.cloud import CloudError, _request
    from framework.create import _dependency
    from framework.skill_pull import _root, pull_skills

    show_messages()
    root = _root(project_path)
    try:
        server, token = _connection(root)
        setup = _request("/api/v1/projects/setup", token, server).json()
    except CloudError as error:
        raise _unreachable(error) from error
    revision = str(setup.get("revision") or "")
    language = setup.get("language") or "python"
    if refusal := _other_language(language, "pull_setup"):
        raise refusal

    cloud = {_key(p["name"]): p for p in setup.get("packages") or [] if isinstance(p, dict)}
    if language != "python":
        cloud = {}
    local, _ = _local(root, cloud) if language == "python" else ({}, {})
    added = [cloud[k] for k in cloud if k not in local]
    differ = [(local[k], cloud[k]) for k in cloud if k in local and not _same(local[k], cloud[k])]
    kept_local = [local[k]["name"] for k in local if k not in cloud]

    with _quiet():
        plan = pull_skills(root, overwrite=overwrite, dry_run=True)

    lines = [f"Setup changes from Framework Cloud (revision {revision}):"]
    if language == "stata":
        lines.append("  Packages: not managed for Stata projects; skills only")
    if added:
        lines.append(f"  Packages to add: {', '.join(p['name'] for p in added)}")
    if differ:
        details = ", ".join(
            f"{c['name']} (local {_describe(lo)}, Cloud {_describe(c)})" for lo, c in differ
        )
        tail = (
            "Cloud's will be used" if overwrite else "kept; pull_setup(overwrite=True) uses Cloud's"
        )
        lines.append(f"  Packages that differ: {details} — {tail}")
    if kept_local:
        lines.append(f"  Only in this project (kept): {', '.join(kept_local)}")
    skills_line = _skills_line(plan["counts"])
    if skills_line:
        lines.append(skills_line)
    changes = bool(added or (differ and overwrite)) or any(
        plan["counts"].get(k) for k in ("added", "updated", "replaced")
    )
    if not changes and not differ and not skills_line and language != "stata":
        lines = [f"Setup is up to date with Framework Cloud (revision {revision})."]
    for line in lines:
        inform(line)

    result: dict[str, Any] = {
        "revision": revision,
        "packages": {
            "added": [p["name"] for p in added],
            "changed": [c["name"] for _, c in differ] if overwrite else [],
            "differ_kept": [] if overwrite else [c["name"] for _, c in differ],
            "kept_local": kept_local,
        },
        "skills": None,
        "applied": False,
        "dry_run": dry_run,
        "message": "\n".join(lines),
    }
    if dry_run:
        return result
    if not changes:
        _lock_write(root, revision, server)
        return result
    if not _ask("Apply these changes?", confirm, "pull_setup"):
        inform("Nothing changed.")
        return result

    requirements = [_dependency(p["name"], p) for p in added]
    if overwrite:
        requirements += [_dependency(c["name"], c) for _, c in differ]
    if requirements:
        _install(root, requirements)
        inform(f"✓ Packages: {len(requirements)} added or updated")
    result["skills"] = pull_skills(root, overwrite=overwrite)
    _lock_write(root, revision, server)
    result["applied"] = True
    return result


def push_setup(
    project_path: str | Path = ".",
    remove: bool = False,
    force: bool = False,
    confirm: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Send the project's packages, then its edited skills, to its setup on Framework Cloud.

    The package list in ``pyproject.toml`` goes to Cloud
    (``POST /api/v1/projects/setup/packages``), first as a dry run whose changes are
    listed with a warning: new setups, zips, and Update on GitHub use what is pushed.
    Then asks "Push these changes?". Packages only on Cloud stay there unless
    ``remove=True``. framework-py and the unpinned workflow tool packages every project
    gets are not sent.

    When Cloud's setup changed since this project last pulled it (the revision in
    ``.framework/setup.lock.json``), the push is refused with "Pull first";
    ``force=True`` replaces Cloud's newer changes. Edited skills then go through
    ``push_skills()``, with its own warning.

    Args:
        project_path: The project's root directory. Default: the current directory.
        remove: Remove packages from Cloud's setup that this project doesn't list.
        force: Push even when Cloud's setup changed since the last pull.
        confirm: True pushes without asking; False cancels. None asks in a console.
        dry_run: List the changes and send nothing.

    Returns:
        A dict: ``revision``, ``changes`` (Cloud's ``added``, ``changed``, ``removed``,
        and ``kept``), ``applied``, ``skills`` (the ``push_skills()`` result),
        ``dry_run``, and ``message``.

    Raises:
        CloudError: Without a project token, offline, when the server has no setup
            endpoint, or when Cloud's setup changed and ``force`` is False.
        FrameworkError: Without a console and without ``confirm=True`` or ``dry_run=True``.
    """
    from framework._log import inform, show_messages
    from framework.cloud import (
        CloudConflictError,
        CloudError,
        CloudOfflineError,
        _MissingEndpointError,
        api,
    )
    from framework.skill_pull import _root, push_skills

    show_messages()
    root = _root(project_path)
    try:
        server, token = _connection(root)
        # The language, and the base revision for a project that never pulled its setup
        setup = api("/api/v1/projects/setup", token, server)
    except (CloudOfflineError, _MissingEndpointError) as error:
        raise _unreachable(error) from error
    except CloudError as error:
        if "project token" in str(error):
            raise _unreachable(error) from error
        raise
    base = _lock_read(root).get("revision") or setup.get("revision")
    if refusal := _other_language(setup.get("language") or "python", "push_setup"):
        raise refusal
    if setup.get("language") == "stata":
        inform("Packages are not managed for Stata projects; pushing skills only.")
        return {
            "revision": setup.get("revision"),
            "changes": {"added": [], "changed": [], "removed": [], "kept": []},
            "applied": False,
            "skills": push_skills(root, force=force, confirm=confirm, dry_run=dry_run),
            "dry_run": dry_run,
            "message": "Packages are not managed for Stata projects.",
        }
    cloud = {_key(p["name"]) for p in setup.get("packages") or [] if isinstance(p, dict)}
    local, _ = _local(root, cloud)

    def send(dry: bool) -> dict[str, Any]:
        body = {
            "base_revision": base,
            "packages": [{**row, "auto_attach": False} for row in local.values()],
            "remove": remove,
            "force": force,
            "dry_run": dry,
        }
        try:
            reply = api("/api/v1/projects/setup/packages", token, server, "POST", body)
        except CloudConflictError as error:
            current = error.body.get("revision")
            raise CloudError(
                "Framework Cloud's setup changed since this project last pulled it "
                f"(revision {current}). Pull first: pull_setup(). "
                "push_setup(force=True) replaces Cloud's changes."
            ) from error
        except (CloudOfflineError, _MissingEndpointError) as error:
            raise _unreachable(error) from error
        return reply if isinstance(reply, dict) else {}

    preview = send(True)
    changes = preview.get("changes") or {}
    added = [p["name"] for p in changes.get("added") or []]
    changed = changes.get("changed") or []
    removed = [p["name"] for p in changes.get("removed") or []]
    kept = [p["name"] for p in changes.get("kept") or []]

    lines = ["Setup changes for Framework Cloud:"]
    if added:
        lines.append(f"  Packages to add: {', '.join(added)}")
    if changed:
        details = ", ".join(
            f"{c['name']} ({_describe(c['from'])} -> {_describe(c['to'])})" for c in changed
        )
        lines.append(f"  Packages to change: {details}")
    if removed:
        lines.append(f"  Packages to remove: {', '.join(removed)}")
    if kept:
        lines.append(f"  On Cloud only (kept): {', '.join(kept)}")
    pending = bool(added or changed or removed)
    result: dict[str, Any] = {
        "revision": preview.get("revision"),
        "changes": {"added": added, "changed": changed, "removed": removed, "kept": kept},
        "applied": False,
        "skills": None,
        "dry_run": dry_run,
    }
    if not pending:
        lines = ["Setup on Framework Cloud already matches this project.", *lines[1:]]
    else:
        lines.append(
            f"This changes {_project_name(root)}'s setup on Framework Cloud. "
            "New setups, zips, and Update on GitHub will use it."
        )
        sent = set(local)
        beyond = [n for n in [*added, *(c["name"] for c in changed)] if _key(n) not in sent]
        if beyond:
            lines.append(
                "Pushing re-resolves the project from its blueprint's latest version, "
                "which can update a project pinned to an older blueprint version."
            )
        if force and base != setup.get("revision"):
            lines.append("Cloud's newer setup changes will be replaced by this project's packages.")
    for line in lines:
        inform(line)
    result["message"] = "\n".join(lines)

    if pending and not dry_run:
        if _ask("Push these changes?", confirm, "push_setup"):
            reply = send(False)
            revision = str(reply.get("revision") or "")
            result.update(applied=bool(reply.get("applied")), revision=revision)
            _lock_write(root, revision, server)
            done = reply.get("changes") or changes
            parts = [
                f"{len(done.get(key) or [])} {key}"
                for key in ("added", "changed", "removed")
                if done.get(key)
            ]
            if reply.get("applied"):
                inform(f"Setup pushed: revision {revision} ({', '.join(parts)})")
            else:
                inform("Setup on Framework Cloud is already up to date.")
        else:
            inform("Setup not pushed.")
            return result

    try:
        result["skills"] = push_skills(root, force=force, confirm=confirm, dry_run=dry_run)
    except CloudError as error:
        # Skills need your login; the package list went with the project token
        inform(f"Skills not pushed: {error}")
    return result
