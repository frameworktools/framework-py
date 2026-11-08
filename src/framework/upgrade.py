"""``upgrade()``: bring a project made by an older Framework to the current layout.

The steps and their order follow R's ``upgrade()`` (``R/upgrade.R``). The plan is
computed against the files on disk plus the writes planned by earlier steps, so
later steps see earlier ones. Nothing is written until the plan is applied, and
every file rewritten is backed up to ``.framework/backups/<timestamp>/`` first.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from framework import _yaml
from framework._log import inform, show_messages
from framework.errors import FrameworkError

REGENERATE = "<!-- @framework:regenerate -->"
_CONNECTION_KEYS = ("databases", "storage_buckets", "default_database", "default_storage_bucket")
_DERIVED = (
    "name",
    "type",
    "author",
    "tools",
    "directories",
    "extra_directories",
    "render_dirs",
    "security",
    "connections",
)


@dataclass
class UpgradeResult:
    """What ``upgrade()`` planned and did.

    Attributes:
        actions: One row per planned change: ``step``, ``path``, and ``change``.
        notes: Things to know that ``upgrade()`` leaves to you.
        applied: Whether the plan was applied.
        backup_location: The folder holding the files as they were, or None.
    """

    actions: pd.DataFrame
    notes: list[str] = field(default_factory=list)
    applied: bool = False
    backup_location: Path | None = None


@dataclass
class _Plan:
    root: Path
    main: str
    pending: dict[str, str | None] = field(default_factory=dict)
    actions: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skills: list[Callable[[], Any]] = field(default_factory=list)
    type: str | None = None
    move_settings: bool = False
    before: dict[str, Any] = field(default_factory=dict)
    secret_names: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)

    def read(self, relative: str) -> str | None:
        """A file's text as planned, else as on disk, else None."""
        if relative in self.pending:
            return self.pending[relative]
        path = self.root / relative
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def write(self, step: str, relative: str, text: str | None, change: str) -> bool:
        """Plan writing ``text`` to a file (None removes it)."""
        if text is not None and self.read(relative) == text:
            return False
        self.pending[relative] = text
        self.actions.append({"step": step, "path": relative, "change": change})
        return True

    def note(self, text: str) -> None:
        self.notes.append(text)

    def yaml(self, relative: str) -> Any:
        """A YAML file's content (``!expr`` as ``"!expr ..."`` strings), or None."""
        text = self.read(relative)
        if text is None:
            return None
        try:
            return _yaml.load(text)
        except Exception:
            return None


# Settings as written ----------------------------------------------------------------


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _scoped(raw: Any) -> bool:
    from framework.settings import _has_environment_sections

    return _has_environment_sections(raw)


def _section(raw: Any) -> dict[str, Any]:
    """The part of the main settings file that holds the settings: ``default:``, or the
    whole file when it has no environment sections."""
    if _scoped(raw):
        default = raw.get("default")
        return default if isinstance(default, dict) else {}
    return raw if isinstance(raw, dict) else {}


def _split_file(value: Any) -> str | None:
    if isinstance(value, str) and re.search(r"\.ya?ml$", value, re.IGNORECASE):
        return value.removeprefix("./")
    return None


def _settings_raw(plan: _Plan) -> dict[str, Any]:
    """The settings as written (``env()`` and ``!expr`` unevaluated): the main section with
    split files read in and stray connection keys lifted."""
    from framework.settings import _lift_connections

    section = dict(_section(plan.yaml(plan.main)))
    for key in list(section):
        relative = _split_file(section[key])
        if relative is None:
            continue
        split = plan.yaml(relative)
        if split is None:
            del section[key]
            continue
        if isinstance(split, dict) and key in split:
            section[key] = split[key]
            for other, value in split.items():
                if other != key and section.get(other) is None:
                    section[other] = value
        else:
            section[key] = split
    return _lift_connections(section)


def _directories(settings: dict[str, Any]) -> dict[str, str]:
    directories = settings.get("directories")
    return {
        str(key): value.removeprefix("./").rstrip("/")
        for key, value in (directories if isinstance(directories, dict) else {}).items()
        if isinstance(value, str) and value
    }


def _set_in_main(plan: _Plan, key: str, value: Any) -> str:
    """The main settings file's text with ``key`` set in ``default:`` (or at the top of a
    flat file), keeping comments and layout."""
    text = plan.read(plan.main) or ""

    def change(data: Any) -> Any:
        data = data if data is not None else {}
        target = data["default"] if _scoped(data) else data
        target[key] = value
        return data

    return _yaml.update_text(text, change)


# Steps ----------------------------------------------------------------------------


def _step_settings_file(plan: _Plan) -> None:
    """A 1.x ``config.yml`` becomes ``settings.yml``."""
    if plan.main != "config.yml":
        return
    plan.write("settings", "settings.yml", plan.read("config.yml"), "create from config.yml")
    plan.write("settings", "config.yml", None, "remove (now settings.yml)")
    plan.main = "settings.yml"


def _step_settings(plan: _Plan, add_language: bool) -> None:
    """Missing current sections: ``language``, ``tools``, and ``security``."""
    from framework._private import settings_sensitive
    from framework.settings import _ignored_keys

    raw = plan.yaml(plan.main)
    if not isinstance(raw, dict):
        plan.note(f"{plan.main} couldn't be read as YAML, so the settings were not upgraded.")
        return
    ignored = _ignored_keys(raw, "default") if _scoped(raw) else []
    if ignored and not plan.move_settings:
        plan.note(
            f"{plan.main} has keys outside default: ({', '.join(ignored)}). Framework "
            "ignores them. upgrade(move_settings=True) moves each into settings/<key>.yml, "
            "named under default:, so settings_read() and settings() read it."
        )
    # Moved first, from the text as written, so the keys keep their layout and comments
    moved = _move_ignored_keys(plan, ignored) if ignored and plan.move_settings else []
    section = _section(raw)
    settings = _settings_raw(plan)
    plan.before = settings
    changes: list[str] = []
    to_sensitive = plan.type == "project_sensitive"
    current_type = str(settings.get("project_type") or "project")
    if plan.type is not None and current_type != plan.type:
        plan.pending[plan.main] = _set_in_main(plan, "project_type", plan.type)
        changes.append(f"set project_type: {plan.type} (was {current_type})")
        if settings_sensitive(settings) and not to_sensitive:
            plan.note(
                "The project stays sensitive (security: sensitive: true): a type is only a "
                "label. Set sensitive: false by hand once it holds no sensitive data."
            )
    if add_language:
        plan.pending[plan.main] = _set_in_main(plan, "language", "python")
        changes.append("add language: python")
    if section.get("tools") is None:
        render = settings.get("render_dirs")
        tools = {
            "quarto": (plan.root / "_quarto.yml").is_file() or bool(render),
            "uv": (plan.root / "pyproject.toml").is_file() or (plan.root / "uv.lock").is_file(),
        }
        plan.pending[plan.main] = _set_in_main(plan, "tools", tools)
        changes.append(
            f"add tools (quarto: {str(tools['quarto']).lower()}, uv: {str(tools['uv']).lower()})"
        )
    sensitive = settings_sensitive(settings) or to_sensitive
    security = section.get("security")
    flag = security.get("sensitive") if isinstance(security, dict) else None
    if to_sensitive and flag is False:
        block = dict(security) if isinstance(security, dict) else {}
        block["sensitive"] = True
        plan.pending[plan.main] = _set_in_main(plan, "security", block)
        changes.append("set security.sensitive: true")
    elif flag is None:
        block = dict(security) if isinstance(security, dict) else {}
        block["sensitive"] = sensitive
        plan.pending[plan.main] = _set_in_main(plan, "security", block)
        if settings.get("project_type") == "project_sensitive" or to_sensitive:
            why = "; project_type is project_sensitive"
        else:
            why = "; the folders use the private layout" if sensitive else ""
        changes.append(f"add security (sensitive: {str(sensitive).lower()}{why})")
    elif sensitive and flag is False:
        plan.note(
            "project_type is project_sensitive but security: sensitive: is false. Framework "
            "treats the project as sensitive; set it to true to match."
        )
    if moved:
        changes.append(f"move {', '.join(moved)} under default: (into settings/)")
    if changes:
        # The edits above went straight to pending; record them as one action
        text = plan.pending.pop(plan.main)
        plan.write("settings", plan.main, text, "; ".join(changes))
    if to_sensitive:
        _private_directories(plan)
    _sensitive_notes(plan)


def _move_ignored_keys(plan: _Plan, keys: list[str]) -> list[str]:
    """Move top-level keys that no environment reads into ``settings/<key>.yml``, their
    text as written (comments kept), and name each file under ``default:``
    (``ehr_source: settings/ehr_source.yml``), as Framework's own split files are. A key
    whose file exists is left and reported. The main file's new text goes to pending;
    returns the keys moved."""
    moved: list[str] = []
    text = plan.read(plan.main) or ""
    for key in keys:
        relative = f"settings/{key}.yml"
        if plan.read(relative) is not None:
            plan.note(
                f"{relative} already exists, so {key}: was left outside default:. Merge it by hand."
            )
            continue
        lines = text.split("\n")
        starts = [i for i, line in enumerate(lines) if line.startswith(f"{key}:")]
        if len(starts) != 1:
            plan.note(
                f"Couldn't find {key}: in {plan.main} to move; move it under default: by hand."
            )
            continue
        start = starts[0]
        # The next top-level key; a block sequence may start at the left edge too
        tops = [
            i for i, line in enumerate(lines) if i > start and re.match(r"^(?!-(\s|$))[^\s#]", line)
        ]
        end = (tops[0] if tops else len(lines)) - 1
        # Blank lines and comments at the left edge before the next key belong to it
        while end > start and (not lines[end].strip() or lines[end].startswith("#")):
            end -= 1
        block = lines[start : end + 1]
        # Comments right above the key travel with it
        above = start - 1
        while above >= 0 and lines[above].startswith("#"):
            above -= 1
        lead = lines[above + 1 : start]
        remaining = lines[: above + 1] + lines[end + 1 :]
        while remaining and not remaining[-1].strip():
            remaining.pop()
        try:
            check = _yaml.load("\n".join(block))
        except Exception:
            check = None
        if not isinstance(check, dict) or list(check) != [key]:
            plan.note(
                f"Couldn't move {key}: out of {plan.main} cleanly; move it under default: by hand."
            )
            continue
        plan.write(
            "settings",
            relative,
            "\n".join([*lead, *block]) + "\n",
            f"create from {plan.main}'s top-level {key}: (named under default:)",
        )
        text = "\n".join(remaining) + "\n"
        moved.append(key)
    if moved:

        def change(data: Any) -> Any:
            for key in moved:
                data["default"][key] = f"settings/{key}.yml"
            return data

        # Cut as text first, so each key moves as written; then one edit names them all
        plan.pending[plan.main] = _yaml.update_text(text, change)
        plan.note(
            f"Moved {', '.join(moved)} into settings/. settings_read() and "
            f'settings("{moved[0]}...") read them; code that read them from {plan.main} with '
            "a YAML reader should use framework.settings_read()."
        )
    return moved


def _private_directories(plan: _Plan) -> None:
    """A project becoming sensitive gets the private and public folders of the
    project_sensitive layout it lacks. Folders it has keep their keys and paths, so code
    that names them keeps working."""
    from framework._gitignore import rules_summary
    from framework.spec.resolve import spec_resolve

    current = _directories(_settings_raw(plan))
    template = spec_resolve(
        [{"type": "project_sensitive", "language": "python"}], "template", check=False
    ).spec
    wanted = {
        key: path
        for key, path in (template.get("directories") or {}).items()
        if re.search(r"_(private|public)_", key)
        and key not in current
        and path not in current.values()
    }
    if not wanted:
        return
    relative = _split_file(_section(plan.yaml(plan.main)).get("directories"))
    if relative is not None:
        wrapped = isinstance(plan.yaml(relative), dict) and "directories" in plan.yaml(relative)

        def change_split(data: Any) -> Any:
            data = data if data is not None else {}
            target = data.setdefault("directories", {}) if wrapped else data
            target.update(wanted)
            return data

        text = _yaml.update_text(plan.read(relative) or "", change_split)
    else:
        relative = plan.main

        def change_main(data: Any) -> Any:
            data = data if data is not None else {}
            target = data["default"] if _scoped(data) else data
            if not isinstance(target.get("directories"), dict):
                target["directories"] = {}
            target["directories"].update(wanted)
            return data

        text = _yaml.update_text(plan.read(relative) or "", change_main)
    plan.write(
        "settings",
        relative,
        text,
        "add the private and public folders of the sensitive layout: "
        + rules_summary(list(wanted), n=4),
    )


def _output_dir_for(settings: dict[str, Any], kind: str) -> str:
    """The folder ``save_table()`` and friends write one kind of output to, from settings
    as written, as ``_paths.output_directory()`` decides it."""
    from framework._private import settings_sensitive

    directories = _directories(settings)
    split = (
        settings_sensitive(settings)
        or f"outputs_private_{kind}" in directories
        or f"outputs_public_{kind}" in directories
    )
    if not split:
        return directories.get(f"outputs_{kind}", f"outputs/{kind}")
    return directories.get(f"outputs_private_{kind}", f"outputs/private/{kind}")


def _old_spec(plan: _Plan) -> dict[str, Any] | None:
    """The spec recorded in ``.framework/spec.json`` (as on disk or planned), or None."""
    text = plan.read(".framework/spec.json")
    if text is None:
        return None
    try:
        spec = json.loads(text)
    except json.JSONDecodeError:
        return None
    return spec if isinstance(spec, dict) else None


def _sensitive_notes(plan: _Plan) -> None:
    """What changes for a project that becomes sensitive: where ``save_table()`` and
    friends write, and the folders that hold data but aren't private."""
    from framework._private import is_private_folder_name, settings_sensitive

    after = _settings_raw(plan)
    if not settings_sensitive(after):
        return
    before = dict(plan.before)
    old = _old_spec(plan)
    if plan.type is None and old is not None and isinstance(old.get("security"), dict):
        # Made sensitive by hand since the last upgrade(): compare with the spec that
        # upgrade() recorded then
        if old["security"].get("sensitive") is True:
            return
        before = {"project_type": old.get("type"), "directories": old.get("directories")}
    elif settings_sensitive(before):
        return
    before["security"] = {"sensitive": False}
    if before.get("project_type") == "project_sensitive":
        before["project_type"] = "project"

    kinds = {
        "tables": "save_table()",
        "figures": "save_figure()",
        "models": "save_model()",
        "reports": "save_report()",
    }
    moved = []
    for kind, function in kinds.items():
        old_dir, new_dir = _output_dir_for(before, kind), _output_dir_for(after, kind)
        if old_dir != new_dir:
            moved.append(f"{function} to {new_dir} (was {old_dir})")
    if moved:
        plan.note(
            "Sensitive projects keep outputs private by default, so these now write to "
            f"private folders: {'; '.join(moved)}. Code that reads the old folders reads "
            "stale copies; point it at the new ones, or pass public=True for output that "
            "may be shared."
        )

    def private(path: str) -> bool:
        return any(is_private_folder_name(part) for part in path.split("/"))

    open_dirs = [
        f"{key} ({path})"
        for key, path in _directories(after).items()
        if (re.match(r"^(inputs|outputs)_", key) or key in ("cache", "scratch"))
        and not re.search("private|public", key)
        and not private(path)
    ]
    if open_dirs:
        plan.note(
            "These folders may hold restricted data but aren't private, so Framework's "
            f"private-data checks and agent guards don't cover them: {', '.join(open_dirs)}. "
            "Move their data under a private folder (inputs/private/..., "
            "outputs/private/...) and update the code that reads it, or rename each key "
            "to its private form (inputs_raw to inputs_private_raw) and keep the path."
        )
    render = [
        f"render_dirs.{key} ({path})"
        for key, path in _mapping(after.get("render_dirs")).items()
        if isinstance(path, str) and not private(path.removeprefix("./").rstrip("/"))
    ]
    if render:
        plan.note(
            "Rendered notebooks can show restricted data, but these render folders aren't "
            f"private: {', '.join(render)}. Point them under outputs/private/ (and the "
            "output-dir in each _quarto.yml)."
        )


def _legacy_connections(raw: Any) -> bool:
    """Whether a connections file (or ``{"connections": ...}``) uses an old layout."""
    if not isinstance(raw, dict):
        return False
    if any(key in raw for key in ("options", *_CONNECTION_KEYS)):
        return True
    connections = raw.get("connections")
    if connections is None:
        return False
    if not isinstance(connections, dict):
        return True
    return any(key not in _CONNECTION_KEYS for key in connections)


def _migrate_connections(raw: Any) -> dict[str, Any]:
    """The current connections block from any older layout. Values keep their form (and
    ``!expr`` tags, from round-trip objects)."""
    connections = _mapping(raw.get("connections"))
    options = _mapping(raw.get("options"))
    flat = {
        k: v for k, v in connections.items() if k not in _CONNECTION_KEYS and isinstance(v, dict)
    }

    def is_bucket(entry: Any) -> bool:
        driver = str(entry.get("driver") or "").lower()
        return driver == "s3" or entry.get("bucket") is not None

    def mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    databases: dict[str, Any] = {}
    buckets: dict[str, Any] = {}
    for source in (
        mapping(connections.get("databases")),
        mapping(raw.get("databases")),
        {k: v for k, v in flat.items() if not is_bucket(v)},
    ):
        for name, entry in source.items():
            databases.setdefault(name, entry)
    for source in (
        mapping(connections.get("storage_buckets")),
        mapping(raw.get("storage_buckets")),
        {k: v for k, v in flat.items() if is_bucket(v)},
    ):
        for name, entry in source.items():
            buckets.setdefault(name, entry)

    def pick(*values: Any) -> str | None:
        return next((str(v) for v in values if isinstance(v, str) and v), None)

    out: dict[str, Any] = {
        "default_database": pick(
            connections.get("default_database"),
            raw.get("default_database"),
            options.get("default_connection"),
            options.get("default_database"),
        )
    }
    bucket = pick(
        connections.get("default_storage_bucket"),
        raw.get("default_storage_bucket"),
        options.get("default_s3_connection"),
        options.get("default_storage_bucket"),
    )
    if bucket is not None:
        out["default_storage_bucket"] = bucket
    out["databases"] = databases
    out["storage_buckets"] = buckets
    return out


def _secret_note(plan: _Plan, raw: Any, relative: str) -> None:
    """A note per written-out credential: the variable the spec refers to it by, and how
    to make the settings match. Values are never shown."""
    from framework.security import _secret_keys, env_call

    for path in _secret_keys(raw, "", False):
        parts = path.split(".")
        parent: Any = raw
        for part in parts[:-1]:
            parent = parent.get(part) if isinstance(parent, dict) else None
        value = parent.get(parts[-1]) if isinstance(parent, dict) else None
        in_section = len(parts) >= 4 and parts[1] in ("databases", "storage_buckets")
        conn = parts[2] if in_section else parts[max(0, len(parts) - 2)]
        driver = parent.get("driver") if isinstance(parent, dict) else None
        name, source = _secret_env_name(plan, conn, parts[-1], value, driver)
        where = (
            "no .env, .env example, or code names one, so it is made from the connection's name"
            if source == "derived"
            else f"the name {source} uses"
        )
        what = (
            "gives env() a default that looks like a credential"
            if env_call(value) is not None
            else "has a password or token written in"
        )
        do = (
            f"{name} is set in .env, so write"
            if source == ".env"
            else f"Set {name} in .env, then write"
        )
        plan.note(
            f'{relative}: {path} {what}. .framework/spec.json refers to it as env("{name}") '
            f'({where}). {do} {parts[-1]}: env("{name}") in {relative} so the settings '
            "match; upgrade() leaves the settings as they are."
        )


def _secret_env_name(
    plan: _Plan, conn: str, key: str, value: Any = None, driver: Any = None
) -> tuple[str, str]:
    """The environment variable a written-out credential belongs in, and where the name
    came from: the name the project already uses for it, else one made from the
    connection (source ``"derived"``). Cached per connection and key, so the plan and the
    spec agree. R: ``.upgrade_secret_env_name()``."""
    from framework.security import env_call

    memo = (conn, key)
    if memo in plan.secret_names:
        return plan.secret_names[memo]
    call = env_call(value)
    derived = re.sub(r"[^A-Za-z0-9]+", "_", f"{conn}_{key}").upper()
    if call is not None and call[0]:
        found = (call[0], "the setting")
    else:
        found = _find_env_name(plan.root, conn, key, value, driver) or (derived, "derived")
    plan.secret_names[memo] = found
    return found


_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_CODE_FILE = re.compile(r"\.(R|r|qmd|Rmd|py|sh|ya?ml)$")
_CODE_SKIP = re.compile(
    r"(^|/)(\.[^/]*|renv|node_modules|packrat|_site|_freeze|[^/]*_files|settings)/"
)


def _small(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size < 512 * 1024
    except OSError:
        return False


def _find_env_name(
    root: Path, conn: str, key: str, value: Any = None, driver: Any = None
) -> tuple[str, str] | None:
    """The variable name the project uses for a credential: from ``.env``, then the
    example ``.env`` files, then the code (R, Quarto, R Markdown, Python, shell, and
    YAML files). A name counts when it fits the key (PASS or PWD for a password, TOKEN
    for a token, ...). The best holds the written-out value (compared, never shown),
    then names the connection and its driver. None when no name fits."""
    from framework.security import env_call

    kind = key.lower()
    if re.search("pass|pwd", kind):
        fits = "PASS|PWD"
    elif "token" in kind:
        fits = "TOKEN"
    elif "secret" in kind:
        fits = "SECRET"
    elif "private" in kind:
        fits = "PRIVATE|KEY"
    elif "access" in kind:
        fits = "ACCESS|KEY"
    elif "api" in kind:
        fits = "API|KEY"
    elif "cred" in kind:
        fits = "CRED"
    else:
        fits = re.escape(re.sub(r"[^A-Za-z0-9]", "", key).upper())
    literal = value if isinstance(value, str) and env_call(value) is None else None

    def read_env(name: str) -> dict[str, str]:
        path = root / name
        if not path.is_file():
            return {}
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = _ENV_ASSIGNMENT.match(line)
            if match:
                values[match.group(1)] = re.sub(r"^(['\"])(.*)\1$", r"\2", match.group(2).strip())
        return values

    candidates: dict[str, list[Any]] = {}

    def add(name: str, source: str, points: int) -> None:
        if not re.search(fits, name):
            return
        entry = candidates.setdefault(name, [source, 0])
        entry[1] += points

    for name, held in read_env(".env").items():
        add(name, ".env", 2 + (4 if literal is not None and held == literal else 0))
    for example in (".env.example", ".env.template", ".env.sample"):
        for name, held in read_env(example).items():
            add(name, example, 1 + (2 if literal is not None and held == literal else 0))
    code: list[str] = []
    for folder, subfolders, files in os.walk(root):
        # Skipped folders aren't walked at all (.venv, renv, node_modules, ...)
        here = Path(folder).relative_to(root).as_posix()
        prefix = "" if here == "." else f"{here}/"
        subfolders[:] = sorted(d for d in subfolders if not _CODE_SKIP.search(f"{prefix}{d}/"))
        code += [
            f"{prefix}{name}"
            for name in sorted(files)
            if _CODE_FILE.search(name) and _small(Path(folder) / name)
        ]
        if len(code) >= 2000:
            break
    code = code[:2000]
    for file in code:
        try:
            text = (root / file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in dict.fromkeys(re.findall(r"[A-Z][A-Z0-9_]{2,}", text)):
            add(name, file, 1)
    if not candidates:
        return None
    conn_tokens = {t.upper() for t in re.split(r"[^A-Za-z0-9]+", conn) if t}
    driver_tokens = {str(driver).upper()} if driver else set()
    if str(driver or "").lower() == "postgres":
        driver_tokens |= {"PG", "PGPASSWORD"}

    def score(item: tuple[str, list[Any]]) -> int:
        parts = item[0].split("_")
        points: int = item[1][1]
        return (
            points
            + 2 * any(p in conn_tokens for p in parts)
            + any(p in driver_tokens for p in parts)
        )

    # The highest score wins; ties go to the first found
    best = max(candidates.items(), key=score)
    return best[0], str(best[1][0])


def _step_connections(plan: _Plan) -> None:
    """Old connection layouts become ``connections: {default_database, databases,
    storage_buckets}``."""
    relative = _split_file(_section(plan.yaml(plan.main)).get("connections"))
    if relative is None:
        connections = _section(plan.yaml(plan.main)).get("connections")
        if isinstance(connections, dict) and _legacy_connections({"connections": connections}):
            plan.note(
                f"The connections in {plan.main} use the old layout. Move them under "
                "connections: databases:, with default_database for the default."
            )
        _secret_note(plan, {"connections": connections}, plan.main)
        return
    raw = plan.yaml(relative)
    if raw is None:
        return
    if not _legacy_connections(raw):
        _secret_note(plan, raw, relative)
        return
    # The round-trip objects keep !expr tags in the rewritten file
    migrated = _migrate_connections(_yaml.round_trip_load(plan.read(relative) or "") or {})
    text = _yaml.round_trip_dump({"connections": migrated})
    default = migrated.get("default_database")
    shown = f", default_database: {default}" if default else ""
    change = f"rewrite in the current layout: {len(migrated['databases'])} database(s){shown}"
    plan.write("connections", relative, text, change)
    _secret_note(plan, {"connections": _migrate_connections(raw)}, relative)


def _step_gitignore(plan: _Plan) -> None:
    """Append the current template's missing rules to ``.gitignore``."""
    from framework._gitignore import missing_rules, rules_summary, unignored_folders
    from framework._private import settings_sensitive
    from framework.security import _outside, folder_destinations
    from framework.spec.resolve import _gitignore

    settings = _settings_raw(plan)
    template = _gitignore(
        str(settings.get("project_type") or "project"), settings_sensitive(settings), []
    )
    private = [
        path
        for key, path in _directories(settings).items()
        if "private" in key and "public" not in key and not _outside(plan.root, path)
    ]
    try:
        published = list(folder_destinations(settings, plan.root).values())
    except Exception:
        published = []
    current = plan.read(".gitignore") or ""
    folders = unignored_folders(plan.root, private + published, text=current)
    wanted = [template, *(f"/{folder}/" for folder in folders)]
    missing = missing_rules(plan.root, wanted, text=current)
    if missing.skipped:
        shown = ", ".join(f"{rule} (git tracks {file})" for rule, file in missing.skipped.items())
        plan.note(f".gitignore: left out {shown} so no tracked file is hidden.")
    if not missing.add:
        return
    from framework._gitignore import merged_text

    text = merged_text(current, missing.add, "# Added by framework.upgrade()")
    change = f"add {len(missing.add)} rule(s): {rules_summary(missing.add)}"
    plan.write("gitignore", ".gitignore", text, change)


def _marked(text: str | None) -> bool:
    return text is not None and REGENERATE in text


def _pointer(text: str | None) -> bool:
    return text is not None and len(text.splitlines()) <= 10 and "@AGENTS.md" in text


def _set_ai_setting(plan: _Plan, key: str, value: Any) -> None:
    """Set ``ai.<key>`` where the ai section lives: its split file, or the main file."""
    relative = _split_file(_section(plan.yaml(plan.main)).get("ai"))
    if relative is not None:
        wrapped = isinstance(plan.yaml(relative), dict) and "ai" in plan.yaml(relative)

        def change(data: Any) -> Any:
            data = data if data is not None else {}
            target = data.setdefault("ai", {}) if wrapped else data
            target[key] = value
            return data

        text = _yaml.update_text(plan.read(relative) or "", change)
    else:
        relative = plan.main
        current = _section(plan.yaml(plan.main)).get("ai")
        block = dict(current) if isinstance(current, dict) else {}
        block[key] = value
        text = _set_in_main(plan, "ai", block)
    plan.write("ai", relative, text, f"set ai.{key}: {value}")


def _regenerate_sections(text: str, spec: dict[str, Any]) -> tuple[str, list[str]]:
    """The context file with its marked sections regenerated from the spec. Sections
    without a generator are left as they are."""
    from framework._agents_md import _data, _environment, _packages

    generators: dict[str, tuple[str, Callable[[dict[str, Any]], str]]] = {
        "Framework Environment": ("environment", _environment),
        "Installed Packages": ("packages", _packages),
        "Data Management": ("data", _data),
    }
    done: list[str] = []
    for title, (label, generate) in generators.items():
        heading = re.escape(f"## {title} {REGENERATE}")
        found = re.search(rf"^{heading}[^\n]*\n.*?(?=\n## |\Z)", text, re.DOTALL | re.MULTILINE)
        if found is None:
            continue
        body = generate(spec).rstrip("\n")
        if not text[found.end() :].strip():
            body += "\n"
        text = text[: found.start()] + body + text[found.end() :]
        done.append(label)
    return text, done


def _step_ai(plan: _Plan, spec: dict[str, Any] | None) -> None:
    """AGENTS.md canonical, CLAUDE.md a pointer, and marked sections regenerated."""
    settings = _settings_raw(plan)
    ai = _mapping(settings.get("ai"))
    if ai.get("enabled") is not True:
        return
    agents, claude = plan.read("AGENTS.md"), plan.read("CLAUDE.md")
    canonical = ai.get("canonical_file") or "AGENTS.md"
    target = None
    if claude is not None and not _pointer(claude) and _marked(claude):
        if agents is None or (_marked(agents) and canonical == "CLAUDE.md"):
            plan.write(
                "ai",
                "AGENTS.md",
                claude,
                "create from CLAUDE.md (AGENTS.md becomes the canonical file)"
                if agents is None
                else "replace with CLAUDE.md, the canonical copy (AGENTS.md becomes canonical)",
            )
            plan.write("ai", "CLAUDE.md", "@AGENTS.md\n", "replace with a pointer to AGENTS.md")
            target = "AGENTS.md"
        elif _marked(agents):
            plan.write(
                "ai",
                "CLAUDE.md",
                "@AGENTS.md\n",
                "replace with a pointer to AGENTS.md (a stale copy of it)",
            )
            target = "AGENTS.md"
        else:
            plan.note(
                "AGENTS.md is your own file and CLAUDE.md holds Framework's guide, so both "
                "were kept and CLAUDE.md stays canonical. To switch, move your notes into "
                "CLAUDE.md, rename it AGENTS.md, and run upgrade() again."
            )
            target = canonical = "CLAUDE.md"
    elif _marked(agents):
        target = "AGENTS.md"
    if target == "AGENTS.md" and canonical != "AGENTS.md":
        _set_ai_setting(plan, "canonical_file", "AGENTS.md")

    if target is not None and spec is not None:
        current = plan.read(target) or ""
        regenerated, done = _regenerate_sections(current, spec)
        # Blank lines alone are no reason to rewrite the file
        if [line for line in regenerated.splitlines() if line.strip()] != [
            line for line in current.splitlines() if line.strip()
        ]:
            plan.write("ai", target, regenerated, f"regenerate marked sections ({', '.join(done)})")


def _step_guards(plan: _Plan) -> None:
    """A sensitive project gets the Claude Code guards whatever its AI settings: anyone
    may open it in Claude Code. The plan says when ``.claude/`` is gitignored, since the
    guards then stay on this computer."""
    from framework._gitignore import _git
    from framework._private import settings_sensitive

    settings = _settings_raw(plan)
    if not settings_sensitive(settings):
        return
    added = _agent_guards(plan, settings)
    ai = _mapping(settings.get("ai"))
    enabled = ai.get("enabled") is True
    if added and (not enabled or "claude" not in (ai.get("assistants") or [])):
        plan.note(
            f"AI assistants are {'set without claude' if enabled else 'off'} in the settings, "
            "but .claude/settings.json gets the guards anyway: anyone can open a sensitive "
            "project in Claude Code. Other assistants have no such file; the AGENTS.md rule "
            "and the framework-sensitive-data skill are their guard."
        )
    skills_planned = any(a["path"].startswith(".claude/skills/") for a in plan.actions)
    shared = [".claude/settings.json"]
    if skills_planned or (plan.root / ".claude/skills").is_dir():
        shared.append(".claude/skills/SKILL.md")
    result = _git(plan.root, "check-ignore", *shared)
    found = (
        set(result.stdout.splitlines()) if result is not None and result.returncode == 0 else set()
    )
    ignored = [p.removesuffix("SKILL.md") for p in shared if p in found]
    if ignored and (added or (plan.root / ".claude/settings.json").is_file()):
        plan.note(
            f"Git ignores files in .claude/ ({', '.join(ignored)}), so they stay on this "
            "computer: collaborators and fresh checkouts get none. To share them, ignore "
            ".claude/* instead of .claude/ and add !.claude/settings.json and "
            "!.claude/skills/ to .gitignore."
        )


def _agent_guards(plan: _Plan, settings: dict[str, Any]) -> bool:
    """Claude Code deny rules for the private folders of a sensitive project: folders
    with a ``private`` (or ``*_private``) segment, folders whose key marks them private,
    and private render folders. Returns whether the guard file changes."""
    from framework.ai import restricted_paths
    from framework.security import _outside

    spec = {
        "directories": _directories(settings),
        "extra_directories": settings.get("extra_directories"),
        "render_dirs": settings.get("render_dirs"),
    }
    restricted = [
        p.rstrip("/") for p in restricted_paths(spec) if not _outside(plan.root, p.rstrip("/"))
    ]
    if not restricted:
        plan.note(
            "No Claude Code guard was added: the settings name no private folders. Map the "
            "folders that hold restricted data to private keys (inputs_private_raw, ...) and "
            "run upgrade() again."
        )
        return False
    deny = [rule for path in restricted for rule in (f"Read(/{path}/**)", f"Edit(/{path}/**)")]
    deny.append("Read(/.env)")
    sandbox_deny = [f"./{path}" for path in restricted] + ["./.env"]
    text = plan.read(".claude/settings.json")
    try:
        current = json.loads(text) if text is not None else {}
    except json.JSONDecodeError:
        current = None
    if not isinstance(current, dict):
        plan.note(
            ".claude/settings.json isn't valid JSON, so the guards for private folders "
            "weren't added."
        )
        return False
    new = json.loads(json.dumps(current))
    permissions = new.setdefault("permissions", {})
    permissions["deny"] = list(dict.fromkeys([*permissions.get("deny", []), *deny]))
    sandbox = new.setdefault("sandbox", {})
    sandbox["enabled"] = True
    sandbox["failIfUnavailable"] = True
    filesystem = sandbox.setdefault("filesystem", {})
    filesystem["denyRead"] = list(dict.fromkeys([*filesystem.get("denyRead", []), *sandbox_deny]))
    if new == current:
        return False
    plan.write(
        "ai",
        ".claude/settings.json",
        json.dumps(new, indent=2) + "\n",
        f"keep Claude Code out of {', '.join(restricted)}",
    )
    return True


def _assistant_dirs(settings: dict[str, Any]) -> tuple[list[str], list[str]]:
    from framework.spec.check import ASSISTANTS

    ai = _mapping(settings.get("ai"))
    assistants = [a for a in (ai.get("assistants") or []) if a in ASSISTANTS]
    dirs = list(dict.fromkeys(ASSISTANTS[a][0] for a in assistants)) or [".agents/skills"]
    return assistants, dirs


def _step_skills(plan: _Plan) -> None:
    """Framework skills: update shipped versions, add missing ones, list edits. A project
    with no skills folder gets none, except a sensitive project, which gets
    framework-sensitive-data."""
    from framework._private import settings_sensitive
    from framework.ai import _shipped
    from framework.skill_sync import install_package, package_status
    from framework.spec.resolve import package_defaults

    settings = _settings_raw(plan)
    ai = _mapping(settings.get("ai"))
    if ai.get("enabled") is not True:
        return
    sensitive = settings_sensitive(settings)
    type = "project_sensitive" if sensitive else str(settings.get("project_type") or "project")
    if type == "bare":
        return
    defaults = package_defaults(type if type != "bare" else "project")
    skills = list(defaults["skills"])
    assistants, dirs = _assistant_dirs(settings)

    def uses_skills(folder: str) -> bool:
        path = plan.root / folder
        return path.is_dir() and any(
            p.is_dir() and p.name != "framework-sensitive-data" for p in path.iterdir()
        )

    # Skills copied to .agents/skills (read by Codex and others) are kept current too,
    # whatever the assistants
    if (plan.root / ".agents/skills").is_dir():
        dirs = list(dict.fromkeys([*dirs, ".agents/skills"]))
    existing = [d for d in dirs if uses_skills(d)]
    if not existing:
        if type != "project_sensitive":
            plan.note(
                "This project has no Framework skills. framework.ai_skills_update() installs "
                f"the ones for its type ({type})."
            )
            return
        # A sensitive project gets the sensitive-data skill whatever it had: it tells
        # agents to stay out of private data
        if not assistants:
            dirs = [".claude/skills"]
        skills = ["framework-sensitive-data"]
        plan.note(
            "This project has no other Framework skills; upgrade() adds or keeps "
            "framework-sensitive-data. framework.ai_skills_update() installs the others for "
            f"its type ({type})."
        )
    else:
        dirs = existing
    edited = []
    for skill in skills:
        source = _shipped(skill)
        if not source.is_dir():
            continue
        for folder in dirs:
            relative = f"{folder}/{skill}"
            try:
                status = package_status(source, plan.root, relative, skill)
            except Exception:
                status = "edited"
            if status in ("create", "update"):
                change = "add" if status == "create" else "update (an unedited older version)"
                plan.actions.append({"step": "skills", "path": relative, "change": change})

                def run(src: Any = source, rel: str = relative, name: str = skill) -> Any:
                    return install_package(src, plan.root, rel, name)

                plan.skills.append(run)
            elif status in ("edited", "account"):
                edited.append(relative)
    if edited:
        plan.note(
            "Skills kept because they were edited (or come from your account): "
            f"{', '.join(edited)}. ai_skills_update() leaves them too."
        )


def _step_quarto(plan: _Plan) -> None:
    """Missing Quarto files and keys, never replacing custom configuration."""
    from framework.quarto import quarto_changes

    settings = _settings_raw(plan)
    render = _mapping(settings.get("render_dirs"))
    tools = _mapping(settings.get("tools"))
    has_root = plan.read("_quarto.yml") is not None
    if not has_root and (not render or tools.get("quarto") is False):
        return
    quarto = settings.get("quarto") if isinstance(settings.get("quarto"), dict) else None
    changes = quarto_changes(
        plan.root,
        str(settings.get("project_type") or "project"),
        render,
        quarto,
        _directories(settings),
        read=plan.read,
    )
    for relative, text in changes.write.items():
        exists = plan.read(relative) is not None
        change = "add missing keys (your other keys and comments stay)" if exists else "create"
        plan.write("quarto", relative, text, change)
    for relative in changes.kept:
        folder = relative.rsplit("/", 1)[0]
        plan.note(
            f"{relative} makes {folder}/ a separate Quarto project and was kept. "
            "quarto_regenerate(overwrite=True) folds it into the root _quarto.yml."
        )


def _spec_connections(
    connections: Any, namer: Callable[[str, str, Any, Any], str] | None = None
) -> dict[str, Any]:
    """Connections for the spec: ``env()`` and ``!expr`` stay as written; a password or
    token written in becomes ``env("NAME")``, since specs travel to Cloud. NAME comes
    from ``namer(connection, key, value, driver)`` when given (``upgrade()`` finds the
    name the project uses), else ``<CONNECTION>_<KEY>``."""
    from framework.security import _literal_secret, credential_key, env_call

    connections = connections if isinstance(connections, dict) else {}
    if _legacy_connections({"connections": connections}):
        connections = _migrate_connections({"connections": connections})

    def scrub(entries: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, entry in (entries if isinstance(entries, dict) else {}).items():
            if isinstance(entry, dict):
                entry = dict(entry)
                for key, value in entry.items():
                    if credential_key(key) and _literal_secret(value):
                        # env("NAME", "default") keeps its variable and loses the default,
                        # which Framework Cloud refuses on a credential
                        call = env_call(value)
                        if call is not None:
                            variable = call[0]
                        elif namer is not None:
                            variable = namer(str(name), str(key), value, entry.get("driver"))
                        else:
                            variable = re.sub(r"[^A-Za-z0-9]+", "_", f"{name}_{key}").upper()
                        entry[key] = f'env("{variable}")'
            out[str(name)] = entry
        return out

    out: dict[str, Any] = {}
    if connections.get("default_database") is not None:
        out["default_database"] = connections["default_database"]
    if connections.get("default_storage_bucket") is not None:
        out["default_storage_bucket"] = connections["default_storage_bucket"]
    out["databases"] = scrub(connections.get("databases"))
    out["storage_buckets"] = scrub(connections.get("storage_buckets"))
    return out


def _spec_from_settings(plan: _Plan, settings: dict[str, Any]) -> dict[str, Any]:
    """A spec built from the settings as written, without secrets."""
    from framework import __version__
    from framework._private import settings_sensitive
    from framework._spec_schema import problems as schema_problems
    from framework._spec_schema import prune as schema_prune
    from framework.ai import _shipped
    from framework.spec.check import ASSISTANTS, is_safe_path, spec_check
    from framework.spec.merge import SpecError
    from framework.spec.resolve import spec_resolve

    type = str(settings.get("project_type") or "project")
    name = str(settings.get("project_name") or plan.root.name)
    assistants, _ = _assistant_dirs(settings)
    ai = _mapping(settings.get("ai"))
    tools = _mapping(settings.get("tools"))
    layer = {
        "type": type,
        "language": "python",
        "core_files": True,
        "settings_layout": "split" if (plan.root / "settings").is_dir() else "flat",
        "tools": {k: v for k, v in tools.items() if isinstance(v, bool)},
        "security": {"sensitive": settings_sensitive(settings)},
        "git": {"use_git": (plan.root / ".git").exists()},
        "ai": {"enabled": ai.get("enabled") is True, "assistants": dict.fromkeys(assistants, True)},
    }
    spec = spec_resolve([layer], name, check=False).spec
    spec["directories"] = {k: v for k, v in _directories(settings).items() if is_safe_path(v)}
    extra: dict[str, Any] = {}
    entries = settings.get("extra_directories")
    if isinstance(entries, dict):
        entries = [{"key": k, **v} for k, v in entries.items() if isinstance(v, dict)]
    for entry in entries if isinstance(entries, list) else []:
        path = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(path, str) and is_safe_path(path):
            from framework.adopt import _extra_key

            key = str(entry.get("key") or _extra_key(path, set(extra)))
            extra[key] = {"label": str(entry.get("label") or path.rsplit("/", 1)[-1]), "path": path}
    spec["extra_directories"] = extra
    render = _mapping(settings.get("render_dirs"))
    spec["render_dirs"] = {
        str(k): v for k, v in render.items() if isinstance(v, str) and v and is_safe_path(v)
    }
    spec["connections"] = _spec_connections(
        settings.get("connections"),
        namer=lambda conn, key, value, driver: _secret_env_name(plan, conn, key, value, driver)[0],
    )
    scaffold = settings.get("scaffold")
    if isinstance(scaffold, dict):
        merged = {**spec.get("scaffold", {}), **_spec_scaffold(scaffold)}
        if merged.get("notebook_format"):
            merged.pop("workflow", None)
        spec["scaffold"] = merged
    if isinstance(settings.get("quarto"), dict):
        spec["quarto"] = settings["quarto"]
    author = settings.get("author")
    if isinstance(author, dict):
        from framework.spec.resolve import author_merge

        spec["author"] = author_merge(spec.get("author"), author)
    dirs = list(
        dict.fromkeys([".claude/skills", ".agents/skills", *(ASSISTANTS[a][0] for a in assistants)])
    )
    planned = {a["path"].rsplit("/", 1)[-1] for a in plan.actions if a["step"] == "skills"}
    shipped = [p.name for p in _shipped("").iterdir() if p.is_dir()]
    spec["skills"] = {
        skill: {"package": True}
        for skill in sorted(shipped)
        if skill in planned or any((plan.root / d / skill).is_dir() for d in dirs)
    }
    spec["env"] = {}
    spec["files"] = {}
    spec["git"]["gitignore_content"] = (plan.read(".gitignore") or "").rstrip("\n")
    # No upgrade provenance: spec.v1 has no place for it
    spec["source"] = {"origin": "python", "framework_version": __version__}
    # Settings may hold more than the spec has room for; the spec keeps what spec.v1
    # allows
    spec = schema_prune(spec)[0]
    problems = list(dict.fromkeys([*spec_check(spec), *schema_problems(spec)]))
    if problems:
        raise SpecError("; ".join(problems))
    return spec


def _spec_scaffold(scaffold: dict[str, Any]) -> dict[str, Any]:
    """The settings' scaffold options that spec.v1 holds, with values of the schema's
    types. Older ``settings/scaffold.yml`` files carry options the spec has no place for
    (``positron: yes``); ``positron: yes`` becomes ``ide: positron`` when no IDE is set,
    and the rest stay in the settings only."""
    import jsonschema

    from framework._spec_schema import schema

    props = schema()["properties"]["scaffold"]["properties"]
    scaffold = dict(scaffold)
    if scaffold.get("positron") is True and scaffold.get("ide") is None:
        scaffold["ide"] = "positron"
    out: dict[str, Any] = {}
    for key, value in scaffold.items():
        if key not in props or key == "workflow":
            continue
        if props[key].get("type") == "string" and isinstance(value, (bool, int, float)):
            value = str(value).lower() if isinstance(value, bool) else str(value)
        if jsonschema.Draft7Validator(props[key]).is_valid(value):
            out[key] = value
    return out


def _same(a: Any, b: Any) -> bool:
    def norm(value: Any) -> str:
        return json.dumps(value if value else {}, sort_keys=True, default=str)

    return norm(a) == norm(b)


def _step_spec(plan: _Plan, built: dict[str, Any] | Exception) -> None:
    """``.framework/spec.json`` from the upgraded settings, holding only what spec.v1
    allows."""
    from framework._spec_schema import prune, ready
    from framework.spec.merge import SpecError

    relative = ".framework/spec.json"
    if isinstance(built, Exception):
        plan.note(f".framework/spec.json was not written: {built}")
        return
    existing = plan.read(relative)
    if existing is None:
        spec, change = built, "create from the settings"
    else:
        try:
            old = json.loads(existing)
        except json.JSONDecodeError:
            old = None
        if not isinstance(old, dict):
            plan.note(
                ".framework/spec.json isn't valid JSON; fix or remove it, then run upgrade() again."
            )
            return
        # Refresh what the settings decide; keep the rest (files, skills, env)
        stale = [key for key in _DERIVED if not _same(old.get(key), built.get(key))]
        spec = {**old, **{key: built.get(key) for key in stale}}
        # The copy of .gitignore follows the file; git's other settings stay
        old_git = old.get("git") if isinstance(old.get("git"), dict) else None
        content = built["git"].get("gitignore_content")
        if old_git is None or old_git.get("gitignore_content") != content:
            spec["git"] = {**(old_git or built["git"]), "gitignore_content": content}
            stale.append("git.gitignore_content")
        # Keys an earlier upgrade() wrote that spec.v1 has no place for, which Framework
        # Cloud refuses (scaffold.positron, source.upgraded_from)
        spec, dropped = prune(spec)
        if not stale and not dropped:
            return
        parts = []
        if stale:
            parts.append(f"refresh {', '.join(stale)} from the settings")
        if dropped:
            parts.append(
                f"drop {', '.join(dropped)} (not part of spec.v1, so Framework Cloud refuses them)"
            )
        change = "; ".join(parts)
    try:
        spec = ready(spec, quiet=True)
    except SpecError as error:
        plan.note(f".framework/spec.json was not written: {error}")
        return
    plan.write("spec", relative, json.dumps(spec, indent=2, ensure_ascii=False) + "\n", change)


# Language ----------------------------------------------------------------------------


def _language(root: Path, main: str) -> bool:
    """Check that this is a Python project. Returns whether the settings need
    ``language: python`` added."""
    from framework.adopt import detect_language

    raw = _yaml.read(root / main) if (root / main).is_file() else None
    language = _section(raw).get("language")
    spec_path = root / ".framework" / "spec.json"
    if language is None and spec_path.is_file():
        try:
            language = json.loads(spec_path.read_text(encoding="utf-8")).get("language")
        except (ValueError, AttributeError):
            language = None
    if language == "python":
        return False
    if language is not None:
        raise FrameworkError(
            f"This is a{'n' if str(language) == 'r' else ''} {language} project. Upgrade it "
            "with that language's Framework (in R: framework::upgrade(); or fw project "
            "upgrade). Nothing was changed."
        )
    detected, source = detect_language(root)
    if detected == "python":
        return True
    found = f" (the files say {detected}, from {source})" if detected else ""
    raise FrameworkError(
        f"The settings name no language, so this reads as an R project{found}. Upgrade it in "
        "R with framework::upgrade(), or add language: python to the settings if it is a "
        "Python project. Nothing was changed."
    )


# Public API --------------------------------------------------------------------------


def _print(plan: _Plan, actions: pd.DataFrame, dry_run: bool, path: str | Path) -> None:
    lines = [f"Upgrade plan for {plan.root}"]
    if actions.empty:
        lines.append("  Nothing to change: the project is up to date.")
    else:
        width = max(len(p) for p in actions["path"])
        for row in actions.itertuples():
            lines.append(f"  {row.step:<11} {row.path:<{width}}  {row.change}")
    if plan.notes:
        lines.append("Notes:")
        lines += [f"  - {note}" for note in plan.notes]
    if dry_run and not actions.empty:
        lines.append(
            "Dry run: nothing was changed. Apply with python -m framework upgrade --apply (or "
            f'framework.upgrade("{path}", dry_run=False)); every file it rewrites is backed '
            "up to .framework/backups/ first."
        )
    print("\n".join(lines), file=sys.stderr)


_TYPES = ("project", "project_sensitive", "course", "presentation", "bare")


def upgrade(
    path: str | Path = ".",
    dry_run: bool = True,
    backup: bool = True,
    type: str | None = None,
    move_settings: bool = False,
) -> UpgradeResult:
    """Bring a project made by an older Framework up to the current layout.

    It prints a plan first and, with ``dry_run=False``, backs up every file it
    rewrites to ``.framework/backups/<timestamp>/`` (which git ignores) and
    applies the plan. Your code, data, and notes are not touched. Running it
    again changes nothing. ``python -m framework upgrade [--apply]
    [--no-backup]`` (what ``fw project upgrade`` runs) does the same.

    The plan covers, in this order:

    - **Settings**: a 1.x ``config.yml`` becomes ``settings.yml``. Missing
      sections are added: ``tools:`` (Quarto and uv, from the files present)
      and ``security:``. A ``project_sensitive`` project, or one with the
      private folder layout, gets ``security: sensitive: true``. Keys outside
      ``default:``, which Framework ignores, are reported, or moved into
      ``settings/`` with ``move_settings=True``.
    - **Connections**: the old layouts (``options: default_connection:`` with
      connections listed flat, or the empty hybrid of both) are rewritten as
      ``connections:`` with ``default_database``, ``databases``, and
      ``storage_buckets``. A password written into the settings is reported (by
      key, never by value) and left where it is. The spec refers to it as
      ``env("NAME")``, NAME being the variable the project already uses for it
      (in ``.env``, an example ``.env``, or the code), else one made from the
      connection's name; the plan names it and says to set it.
    - **.gitignore**: the rules of the current template for the project's type
      that it lacks, private folders, and folder publishing destinations are
      appended in one block. No line is removed, and a rule that would ignore
      files git tracks is left out and reported.
    - **AI files**: AGENTS.md becomes the canonical context file, with CLAUDE.md
      a one-line ``@AGENTS.md`` pointer, when CLAUDE.md held the full guide;
      the marked sections are regenerated. A hand-written AGENTS.md is left
      alone and reported. Sensitive projects get the Claude Code guards
      (``.claude/settings.json``) that keep agents out of private folders,
      whatever the AI settings say: folders with a ``private`` (or
      ``*_private``) segment, folders whose key marks them private
      (``inputs_private_raw: inputs/raw``), and private render folders. The
      plan says when git ignores ``.claude/``, so the guards would stay on one
      computer.
    - **Skills**: Framework skills that match a version framework-py has
      shipped are updated, in the assistants' skills folders and in
      ``.agents/skills``; missing ones for the type are added, and edited ones
      are kept and listed. A project with no skills folder gets none, except
      that a sensitive project gets ``framework-sensitive-data``.
    - **Quarto**: missing Quarto files are created, and an existing
      ``_quarto.yml`` at most gains missing keys (see ``quarto_regenerate()``).
    - **Spec**: ``.framework/spec.json`` is written from the settings when it is
      missing, and refreshed when its folders, type, author, security, tools,
      connections, or copy of ``.gitignore`` no longer match, so ``cloud_register()`` and
      ``fw project push-spec`` work. It holds only what spec.v1 allows (see
      ``spec.v1.json``), so keys an earlier ``upgrade()`` wrote that Framework
      Cloud refuses (``scaffold.positron``, ``source.upgraded_from``) are
      dropped.

    Args:
        path: The project folder.
        dry_run: Print the plan and change nothing.
        backup: Copy every file before it is rewritten or removed. Skills replaced
            by an update are always backed up, to ``.framework/skills-backups/``.
        type: Change the project's type: ``"project"``, ``"project_sensitive"``,
            ``"course"``, ``"presentation"``, or ``"bare"``. None (default) keeps it.
            Making a project ``project_sensitive`` sets ``security: sensitive: true``,
            adds the private and public folders of that layout it lacks (folders it has
            keep their keys and paths), and then gets the sensitive ``.gitignore``
            rules, agent guards, and skill like any sensitive project. The plan says
            where ``save_table()``, ``save_figure()``, ``save_model()``, and
            ``save_report()`` will write from then on (``outputs/private/...``), and
            lists the data and render folders that aren't private. A sensitive project
            made another type stays sensitive.
        move_settings: Move top-level keys of the settings file that no environment
            reads (``ehr_source:`` beside ``default:``) into ``settings/<key>.yml``,
            keeping their text and comments, and name each file under ``default:``
            (``ehr_source: settings/ehr_source.yml``), so ``settings_read()`` and
            ``settings()`` read them. False (default) reports them.

    Returns:
        An ``UpgradeResult``: the planned ``actions`` (``step``, ``path``, ``change``),
        ``notes``, whether it was ``applied``, and the ``backup_location``.

    Raises:
        FrameworkError: When the folder is not a Framework project, or is a project in
            another language.
    """
    from framework._backups import backup_dir, backup_files
    from framework.project import settings_files

    show_messages()
    if type is not None and type not in _TYPES:
        raise FrameworkError(f'type must be one of {", ".join(_TYPES)}, not "{type}".')
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise FrameworkError(f"Not a folder: {root}")
    found = settings_files(root)
    if not found:
        raise FrameworkError(
            f"Not a Framework project (no settings.yml or Framework config.yml): {root}. To "
            "make it one, use framework.adopt()."
        )
    plan = _Plan(root, found[0].name, type=type, move_settings=move_settings)
    add_language = _language(root, plan.main)

    _step_settings_file(plan)
    _step_settings(plan, add_language)
    _step_connections(plan)
    _step_gitignore(plan)
    try:
        built: dict[str, Any] | Exception = _spec_from_settings(plan, _settings_raw(plan))
    except Exception as error:
        built = error
    _step_ai(plan, None if isinstance(built, Exception) else built)
    _step_skills(plan)
    _step_guards(plan)
    _step_quarto(plan)
    # Built again: the skills and .gitignore it records come from the steps above
    try:
        built = _spec_from_settings(plan, _settings_raw(plan))
    except Exception as error:
        built = error
    _step_spec(plan, built)

    actions = pd.DataFrame(plan.actions, columns=["step", "path", "change"])
    _print(plan, actions, dry_run, path)
    result = UpgradeResult(actions, plan.notes)
    if dry_run or actions.empty:
        return result

    if backup:
        relative = backup_dir(root)
        if backup_files(root, list(plan.pending), relative):
            result.backup_location = root / relative
    for relative, text in plan.pending.items():
        target = root / relative
        if text is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    for install in plan.skills:
        install()
    result.applied = True
    where = (
        f". Backups: {result.backup_location.relative_to(root).as_posix()}"
        if result.backup_location
        else ""
    )
    inform(f"[ok] Upgraded {root}{where}")
    inform("Review the changes (git diff), then commit them.")
    return result
