"""Creating Python projects from a resolved spec, a layers file, or ``new()``.

Python builds the project itself, the way the R package does. The settings
files, data catalog, ``framework.db``, folder layout, AI files, and Quarto
files use the shared formats. The language-specific files are
``scaffold.py``, ``pyproject.toml``, and Python notebook stubs.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from framework import _yaml, db
from framework._log import inform, show_messages
from framework.ai import write_ai_files
from framework.config_home import global_settings, project_register
from framework.errors import FrameworkError
from framework.quarto import quarto_generate_all
from framework.spec.check import spec_check
from framework.spec.merge import SpecError
from framework.spec.resolve import TYPES, author_normalize, spec_resolve

CHECKOUT_ALLOWLIST = frozenset({".git", ".github", ".framework", "README.md", "LICENSE"})
_FILE_LIKE = re.compile(r"\.(qmd|Rmd|R|md|py|ipynb)$")
_TEMPLATE_VARS = ("project_name", "author", "author_email", "affiliation", "date")
_SPLIT_TYPES = ("project", "project_sensitive")
# Lines that ignore .env (or re-include it with a leading "!"), as in R.
_ENV_RULE = re.compile(r"^(!?)/?(\*\*/)?(\.env\*?|\*\.env)$")


def _version() -> str:
    from framework import __version__

    return __version__


def kebab_case(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "project"


def _write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def fill_template(content: str, spec: Mapping[str, Any]) -> str:
    """Replace ``{{ variable }}`` for the fixed set of variables only.

    Other double-brace text, such as Quarto shortcodes, is left alone.
    """
    author = spec.get("author") or {}
    values = {
        "project_name": spec.get("name", ""),
        "author": author.get("name", ""),
        "author_email": author.get("email", ""),
        "affiliation": author.get("affiliation", ""),
        "date": f"{date.today():%Y-%m-%d}",
    }
    for name in _TEMPLATE_VARS:
        replacement = str(values[name])
        # Escape backslashes: re.sub would read them as group references.
        content = re.sub(r"\{\{\s*" + name + r"\s*\}\}", replacement.replace("\\", "\\\\"), content)
    return content


def _stub(name: str) -> str:
    return resources.files("framework").joinpath(f"templates/stubs/{name}").read_text("utf-8")


# The steps --------------------------------------------------------------------


def _assert_checkout(root: Path) -> None:
    if not root.exists():
        return
    unexpected = sorted(p.name for p in root.iterdir() if p.name not in CHECKOUT_ALLOWLIST)
    if unexpected:
        raise FrameworkError(
            f"Refusing to create a project in '{root}': it already contains "
            f"{', '.join(unexpected)}. Headless creation only runs in a fresh checkout "
            f"(allowed: {', '.join(sorted(CHECKOUT_ALLOWLIST))})."
        )


def _create_directories(root: Path, spec: Mapping[str, Any]) -> None:
    for path in (spec.get("directories") or {}).values():
        if path and not path.startswith("outputs/") and not _FILE_LIKE.search(path):
            (root / path).mkdir(parents=True, exist_ok=True)
    for entry in (spec.get("extra_directories") or {}).values():
        if entry.get("path"):
            (root / entry["path"]).mkdir(parents=True, exist_ok=True)
    for key, path in (spec.get("render_dirs") or {}).items():
        if path and "notebook" in key.lower() and not _FILE_LIKE.search(path):
            (root / path).mkdir(parents=True, exist_ok=True)


def _sections(spec: Mapping[str, Any]) -> dict[str, Any]:
    """The settings sections a Python project writes, in the shared format."""
    scaffold = spec.get("scaffold") or {}
    git = spec.get("git") or {}
    ai = spec.get("ai") or {}
    return {
        "author": author_normalize(spec.get("author")),
        "git": {
            "enabled": git.get("use_git", True),
            "user_name": "",
            "user_email": "",
            "hooks": git.get("hooks") or {},
        },
        "ai": {
            "enabled": ai.get("enabled", False),
            "canonical_file": "AGENTS.md",
            "assistants": list(ai.get("assistants") or {}),
        },
        "scaffold": {
            "seed_on_scaffold": scaffold.get("seed_on_scaffold", False),
            "seed": scaffold.get("seed", ""),
            "source_all_functions": scaffold.get("source_all_functions", True),
            "notebook_format": scaffold.get("notebook_format", "quarto"),
        },
        "quarto": spec.get("quarto") or {},
        "connections": spec.get("connections") or {},
    }


def _write_settings(root: Path, spec: Mapping[str, Any]) -> None:
    sections = _sections(spec)
    main: dict[str, Any] = {
        "project_name": spec["name"],
        "project_type": spec["type"],
        "language": spec["language"],
        "directories": spec.get("directories") or {},
    }
    extra = [
        {"key": key, "label": entry.get("label", key), "path": entry["path"]}
        for key, entry in (spec.get("extra_directories") or {}).items()
    ]
    if extra:
        main["extra_directories"] = extra
    main["render_dirs"] = spec.get("render_dirs") or {}

    if spec["type"] in _SPLIT_TYPES:
        for section in ("author", "git", "ai", "scaffold", "quarto"):
            main[section] = f"settings/{section}.yml"
            _yaml.write(root / "settings" / f"{section}.yml", {section: sections[section]})
        main["connections"] = "settings/connections.yml"
        _yaml.write(root / "settings/connections.yml", {"connections": sections["connections"]})
        main["data"] = "settings/data.yml"
        _yaml.write(root / "settings/data.yml", {"data": {}})
    else:
        for section in ("author", "quarto", "git", "ai", "scaffold"):
            main[section] = sections[section]
        main["data"] = {}
        main["connections"] = sections["connections"]

    main["security"] = spec.get("security") or {"sensitive": False}
    if spec.get("data_hooks"):
        main["data_hooks"] = spec["data_hooks"]
    _yaml.write(root / "settings.yml", {"default": main})


def _write_env(root: Path, spec: Mapping[str, Any]) -> None:
    lines = [
        f"{key.upper()}={value if value is not None else ''}"
        for key, value in (spec.get("env") or {}).items()
    ]
    _write(root, ".env", "\n".join(lines) + ("\n" if lines else ""))


def _env_rule(line: str) -> bool | None:
    """Whether a .gitignore line ignores ``.env`` (True), re-includes it (False), or neither."""
    match = _ENV_RULE.match(line.strip())
    return None if match is None else not match.group(1)


def ensure_secrets_ignored(root: Path) -> bool:
    """Make sure ``.gitignore`` ignores ``.env``. Returns whether a rule was added.

    The last rule that mentions ``.env`` decides, as in git, so a layer's
    ``!.env`` cannot bring the secrets file back into a commit.
    """
    path = root / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    rules = [rule for line in text.splitlines() if (rule := _env_rule(line)) is not None]
    if rules and rules[-1]:
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n# Secrets (added by Framework)\n.env\n", encoding="utf-8")
    inform("  Added .env to .gitignore")
    return True


def _scaffold_py(spec: Mapping[str, Any]) -> str:
    return (
        '"""Project setup, run by framework.scaffold() after the settings are loaded.\n\n'
        "Put code here that every notebook and script needs, such as plotting styles.\n"
        "Random seeds are set from settings/scaffold.yml (seed_on_scaffold and seed).\n"
        '"""\n\n'
        "# import matplotlib.pyplot as plt\n"
        '# plt.style.use("seaborn-v0_8-whitegrid")\n'
    )


def _dependency(name: str, entry: Mapping[str, Any]) -> str:
    if entry.get("source") == "github" and entry.get("repo"):
        ref = f"@{entry['ref']}" if entry.get("ref") else ""
        return f"{name} @ git+https://github.com/{entry['repo']}{ref}"
    version = entry.get("version")
    if version and not re.match(r"^[<>=!~]", str(version)):
        version = f"=={version}"
    return f"{name}{version or ''}"


def _pyproject(spec: Mapping[str, Any]) -> str:
    packages = (spec.get("packages") or {}).get("default_packages") or {}
    dependencies = ["framework-py", *(_dependency(n, e) for n, e in packages.items())]
    lines = [
        "[project]",
        f'name = "{kebab_case(spec["name"])}"',
        'version = "0.1.0"',
        f"description = {json.dumps(spec['name'])}",
        'requires-python = ">=3.11"',
        "dependencies = [",
        *(f"    {json.dumps(dependency)}," for dependency in dependencies),
        "]",
        "",
        "[tool.uv]",
        "package = false",
        "",
    ]
    return "\n".join(lines)


def _code_workspace() -> str:
    workspace = {
        "folders": [{"path": "."}],
        "settings": {
            "python.defaultInterpreterPath": ".venv/bin/python",
            "files.associations": {"*.qmd": "quarto"},
        },
    }
    return json.dumps(workspace, indent=2) + "\n"


def _presentation(spec: Mapping[str, Any]) -> str:
    author = (spec.get("author") or {}).get("name") or "Your Name"
    text = _stub("notebook-revealjs.qmd")
    return (
        text.replace("{{ title }}", str(spec["name"]))
        .replace("{{ author }}", author)
        .replace("{{ date }}", f"{date.today():%Y-%m-%d}")
    )


def _record(root: Path, spec: Mapping[str, Any]) -> None:
    record = dict(spec)
    source = dict(record.get("source") or {"origin": "python"})
    source["framework_version"] = _version()
    record["source"] = source
    _write(root, ".framework/spec.json", json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def _git_init(root: Path) -> None:
    if shutil.which("git") is None:
        inform("  Git not found - skipping repository initialization")
        return

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )

    git("init")
    inform("  Initialized git repository")
    from framework.hooks import git_hooks_install

    if git_hooks_install(root / "settings.yml", verbose=False):
        inform("  Installed git hooks")
    ensure_secrets_ignored(root)
    if (root / ".env").exists() and git("check-ignore", "-q", ".env").returncode != 0:
        raise FrameworkError(
            ".env is not ignored by .gitignore, so it would be committed. "
            "Fix .gitignore, then run git init yourself."
        )
    git("add", ".")
    if (
        not git("config", "user.name").stdout.strip()
        or not git("config", "user.email").stdout.strip()
    ):
        inform("  Skipping initial commit (git user.name/user.email not set).")
        return
    if git("commit", "-m", "Initial commit from Framework").returncode == 0:
        inform("  Created initial commit")


def _is_temporary(root: Path) -> bool:
    temp = Path(tempfile.gettempdir()).resolve()
    resolved = root.resolve()
    return resolved.is_relative_to(temp) or str(resolved).startswith(("/tmp/", "/private/tmp/"))


# Public API -----------------------------------------------------------------


def project_create(
    spec: Mapping[str, Any], location: str | Path, *, headless: bool = False
) -> Path:
    """Create a Python project from a resolved spec.

    Args:
        spec: A resolved spec, from ``spec_resolve()``.
        location: The project folder. It must not exist, except in headless
            mode, where it may be a fresh checkout holding only ``.git``,
            ``.github``, ``.framework``, ``README.md``, and ``LICENSE``.
        headless: For automation (the framework.pub GitHub Action): no git
            initialization, no registry entry, and no files under
            ``.github/workflows/``.

    Returns:
        The project folder.
    """
    show_messages()
    problems = spec_check(dict(spec))
    if problems:
        raise SpecError("The project spec is invalid:\n" + "\n".join(f"  - {p}" for p in problems))

    root = Path(location).expanduser()
    files = spec.get("files") or {}
    if headless:
        _assert_checkout(root)
        workflows = sorted(path for path in files if path.startswith(".github/workflows/"))
        if workflows:
            raise FrameworkError(
                "Headless creation cannot write workflow files (GitHub rejects them from "
                f"GITHUB_TOKEN pushes): {', '.join(workflows)}"
            )
    elif root.exists():
        raise FrameworkError(f"Project directory already exists: {root}")
    root.mkdir(parents=True, exist_ok=True)
    inform(f"Creating project in: {root}")

    bare = spec["type"] == "bare"
    _create_directories(root, spec)
    _write_settings(root, spec)
    if not bare:
        _write_env(root, spec)
    _write(root, ".gitignore", str((spec.get("git") or {}).get("gitignore_content", "")) + "\n")
    ensure_secrets_ignored(root)
    if not bare and (spec.get("ai") or {}).get("enabled"):
        write_ai_files(root, dict(spec))
    if not bare:
        _write(root, "scaffold.py", _scaffold_py(spec))
    _write(root, "pyproject.toml", _pyproject(spec))
    ide = str((spec.get("scaffold") or {}).get("ide") or "")
    if not bare and re.search("vscode|positron", ide, re.IGNORECASE):
        _write(root, f"{kebab_case(spec['name'])}.code-workspace", _code_workspace())
    if spec["type"] == "presentation":
        _write(root, "presentation.qmd", _presentation(spec))

    # Blueprint files last, so they replace generated files of the same name.
    for relative, entry in files.items():
        content = entry["content"]
        _write(root, relative, fill_template(content, spec) if entry.get("template") else content)

    if not bare:
        quarto_generate_all(
            root,
            spec["type"],
            spec.get("render_dirs") or {},
            spec.get("quarto") or {},
            spec.get("directories") or {},
        )
        db.ensure_db(root)
    _record(root, spec)

    if not headless:
        if (spec.get("git") or {}).get("use_git"):
            _git_init(root)
        if not _is_temporary(root):
            project_register(root)
    inform(f"Project created: {root}")
    return root


def project_create_from_layers(
    layers_file: str | Path, path: str | Path = ".", interactive: bool = False
) -> Path:
    """Create a project from a layers file (the format framework.pub sends).

    With ``interactive=False`` (the default) it runs headless for automation,
    as ``project_create(headless=True)`` describes.
    """
    source = Path(layers_file)
    try:
        envelope = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FrameworkError(f"Failed to read layers file '{source}': {error}") from error
    if envelope.get("spec_version") != 1:
        raise FrameworkError(
            f"Unsupported spec_version in '{source}': {envelope.get('spec_version')} "
            "(this framework reads 1)"
        )
    if not envelope.get("name"):
        raise FrameworkError(f"Layers file '{source}' has no project name")
    resolved = spec_resolve(envelope.get("layers") or [], envelope["name"], envelope.get("author"))
    spec = resolved.spec
    if envelope.get("source") is not None:
        spec["source"] = envelope["source"]
    return project_create(spec, path, headless=not interactive)


def user_layer(type: str) -> dict[str, Any]:
    """The user's defaults from the global settings, as a spec layer.

    Reads ``author`` and the ``defaults`` section (scaffold, AI assistants,
    git, and the directories for this project type) of ``settings.yml`` in
    ``config_dir()``.
    """
    settings = global_settings()
    raw_defaults = settings.get("defaults")
    defaults: dict[str, Any] = raw_defaults if isinstance(raw_defaults, dict) else {}
    layer: dict[str, Any] = {}
    if isinstance(settings.get("author"), dict):
        layer["author"] = author_normalize(settings["author"])
    scaffold = defaults.get("scaffold")
    if isinstance(scaffold, dict):
        keys = ("seed_on_scaffold", "seed", "source_all_functions", "ide")
        layer["scaffold"] = {k: scaffold[k] for k in keys if k in scaffold}
        if "seed" in layer["scaffold"]:
            layer["scaffold"]["seed"] = str(layer["scaffold"]["seed"])
    ai = defaults.get("ai")
    if isinstance(ai, dict):
        assistants = ai.get("assistants")
        layer["ai"] = {
            key: value for key, value in (("enabled", ai.get("enabled")),) if value is not None
        }
        if isinstance(assistants, list):
            layer["ai"]["assistants"] = {name: True for name in assistants}
    git = defaults.get("git")
    if isinstance(git, dict):
        layer["git"] = {
            k: v
            for k, v in (("use_git", git.get("initialize")), ("hooks", git.get("hooks")))
            if v is not None
        }
    types = settings.get("project_types")
    type_config = types.get(type) if isinstance(types, dict) else None
    if isinstance(type_config, dict) and isinstance(type_config.get("directories"), dict):
        layer["directories"] = type_config["directories"]
    return layer


def new(
    name: str | None = None,
    location: str | Path | None = None,
    type: str | None = None,
    *,
    key: str | None = None,
    interactive: bool | None = None,
) -> Path:
    """Create a new Python project with your defaults.

    Args:
        name: Project name. Asked for when interactive.
        location: The project folder to create. Asked for when interactive.
        type: project, project_sensitive, course, presentation, or bare. When
            left out, it is asked for interactively and is ``bare`` otherwise,
            as in R's ``new()``.
        key: A Framework Cloud project token. The project's definition (its
            layers) is fetched from the cloud, and the token is saved in the
            new project's ``.env`` for publishing and the ledger.
        interactive: Whether to prompt. Default: whether stdin is a terminal.

    Returns:
        The project folder.
    """
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if key is not None:
        return _new_from_cloud(key, location, interactive)
    if name is None:
        if not interactive:
            raise FrameworkError("Project name is required in non-interactive mode")
        name = input("Project name: ").strip()
        if not name:
            raise FrameworkError("Project name cannot be empty")
    if location is None:
        if not interactive:
            raise FrameworkError("Project location is required in non-interactive mode")
        suggested = Path("~") / "projects" / kebab_case(name)
        location = input(f"Project location [{suggested}]: ").strip() or suggested
    if type is None:
        if interactive:
            choices = ", ".join(TYPES)
            type = input(f"Project type ({choices}) [bare]: ").strip() or "bare"
        else:
            type = "bare"
    if type not in TYPES:
        raise FrameworkError(f"Invalid project type. Must be one of: {', '.join(TYPES)}")

    layers: Sequence[Any] = [
        {"kind": "user", "layer": user_layer(type)},
        {"kind": "project", "layer": {"type": type, "language": "python"}},
    ]
    spec = spec_resolve(layers, name).spec
    spec["source"] = {"origin": "python"}
    inform(f"Creating {type} project: {name}")
    root = project_create(spec, location)
    if type != "bare":
        from framework.cloud import register_project

        register_project(root, name, type)
    return root


def _new_from_cloud(key: str, location: str | Path | None, interactive: bool) -> Path:
    from framework.cloud import fetch_layers, write_project_token

    envelope, server = fetch_layers(key)
    name = envelope.get("name")
    if not name:
        raise FrameworkError("The cloud project has no name")
    if location is None:
        suggested = Path("~") / "projects" / kebab_case(str(name))
        answer = input(f"Location [{suggested}]: ").strip() if interactive else ""
        location = answer or suggested
    resolved = spec_resolve(envelope.get("layers") or [], str(name), envelope.get("author"))
    spec = resolved.spec
    spec["source"] = envelope.get("source") or {"origin": "cloud"}
    root = project_create(spec, location)
    if spec["type"] != "bare":
        write_project_token(root, key, server)
    inform(f"Project ready at {root}")
    return root


def project_add_directory(project_path: str | Path, key: str, label: str, path: str) -> Path:
    """Create a folder in a project and record it under ``extra_directories``.

    Args:
        project_path: The project root.
        key: A short key for the folder, such as ``interviews``.
        label: A human-readable name.
        path: The folder, relative to the project root.

    Returns:
        The created folder.
    """
    from framework.spec.check import is_safe_path

    root = Path(project_path)
    if not is_safe_path(path):
        raise FrameworkError(f"Path must be relative and stay inside the project: {path}")
    settings_file = next(
        (root / name for name in ("settings.yml", "config.yml") if (root / name).is_file()), None
    )
    if settings_file is None:
        raise FrameworkError("Project config not found (checked settings.yml and config.yml)")
    folder = root / path
    folder.mkdir(parents=True, exist_ok=True)

    raw = _yaml.read(settings_file) or {}
    config = raw["default"] if isinstance(raw.get("default"), dict) else raw
    extra = [e for e in (config.get("extra_directories") or []) if e.get("key") != key]
    extra.append({"key": key, "label": label, "path": path})
    config["extra_directories"] = extra
    _yaml.write(settings_file, raw)
    return folder
