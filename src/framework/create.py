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
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from framework import _yaml, db
from framework._log import inform, show_messages
from framework.ai import write_ai_files
from framework.config_home import config_dir, global_settings, project_register
from framework.errors import FrameworkError
from framework.quarto import quarto_generate_all
from framework.spec.check import ASSISTANTS, spec_check
from framework.spec.merge import SpecError
from framework.spec.resolve import (
    TYPES,
    author_normalize,
    spec_core_files,
    spec_resolve,
    spec_settings_layout,
    spec_tool_on,
)

CHECKOUT_ALLOWLIST = frozenset({".git", ".github", ".framework", "README.md", "LICENSE"})
# Python's built-in workflow for these structures. Course and presentation
# are Quarto structures, and bare has no notebooks.
DEFAULT_JUPYTER = frozenset({"project", "project_sensitive"})
# "both" is RStudio plus VS Code. Python never writes an .Rproj.
WORKSPACE_IDES = frozenset({"vscode", "positron", "both"})
FILE_LIKE = re.compile(r"\.(qmd|Rmd|R|md|py|ipynb)$")
_TEMPLATE_VARS = ("project_name", "author", "author_email", "affiliation", "date")
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
        if path and not path.startswith("outputs/") and not FILE_LIKE.search(path):
            (root / path).mkdir(parents=True, exist_ok=True)
    for entry in (spec.get("extra_directories") or {}).values():
        if entry.get("path"):
            (root / entry["path"]).mkdir(parents=True, exist_ok=True)
    for key, path in (spec.get("render_dirs") or {}).items():
        if path and "notebook" in key.lower() and not FILE_LIKE.search(path):
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
            **({"workflow": scaffold["workflow"]} if scaffold.get("workflow") else {}),
            "notebook_format": scaffold.get("notebook_format")
            or ("quarto" if spec_tool_on(spec, "quarto") else "jupyter"),
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
    }
    if spec.get("tools"):
        main["tools"] = dict(spec["tools"])
    main["directories"] = spec.get("directories") or {}
    extra = [
        {"key": key, "label": entry.get("label", key), "path": entry["path"]}
        for key, entry in (spec.get("extra_directories") or {}).items()
    ]
    if extra:
        main["extra_directories"] = extra
    main["render_dirs"] = spec.get("render_dirs") or {}

    if spec_settings_layout(spec) == "split":
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

    if spec.get("publish") is not None:
        main["publish"] = spec["publish"]
    main["security"] = spec.get("security") or {"sensitive": False}
    if spec.get("data_hooks"):
        main["data_hooks"] = spec["data_hooks"]
    _yaml.write(root / "settings.yml", {"default": main})


def _ignore_publish_folders(root: Path, spec: Mapping[str, Any]) -> None:
    """Add each folder publishing destination inside the project to ``.gitignore``, as the
    security audit expects: rendered notebooks there can show private results. A public
    destination (meant to be committed, such as a GitHub Pages ``docs/``) is left alone,
    except in a sensitive project."""
    from framework.security import folder_destinations

    folders = folder_destinations(
        {"publish": spec.get("publish"), "security": spec.get("security")}, root
    )
    if not folders:
        return
    path = root / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    existing = {line.strip() for line in text.splitlines()}
    added = [f"/{folder}/" for folder in folders.values() if f"/{folder}/" not in existing]
    if not added:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(
        text + "\n# Folder publishing destinations\n" + "\n".join(added) + "\n", encoding="utf-8"
    )


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


def ensure_secrets_ignored(root: Path, message: str = "  Added .env to .gitignore") -> bool:
    """Make sure ``.gitignore`` ignores ``.env``. Returns whether a rule was added.

    The last rule that mentions ``.env`` decides, as in git, so a layer's
    ``!.env`` cannot bring the secrets file back into a commit.
    """
    path = root / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    rules = [rule for line in text.splitlines() if (rule := _env_rule(line)) is not None]
    if rules and rules[-1]:
        return False
    _add_env_rule(path, text, message)
    return True


def keep_env_out_of_git(root: Path) -> bool:
    """Make sure git ignores ``.env`` before a secret goes in. Returns whether a rule was added.

    In a git repository, git itself decides (``git check-ignore``), so global
    and nested ignore files count. Without git, or outside a repository,
    ``.gitignore`` is still created or extended, so a later ``git init`` picks
    it up.

    Raises:
        FrameworkError: When git already tracks ``.env``.
    """
    message = "Added .env to .gitignore so the project token stays out of git"
    if shutil.which("git") is not None:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", ".env"],
            capture_output=True,
            check=False,
        )
        if tracked.returncode == 0:
            raise FrameworkError(
                "git already tracks .env, so a project token written there would be "
                "committed. Stop tracking it first:\n"
                "  git rm --cached .env\n  git commit -m 'Stop tracking .env'"
            )
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", ".env"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return False
        if result.returncode == 1:
            # A repository that doesn't ignore .env
            path = root / ".gitignore"
            _add_env_rule(path, path.read_text(encoding="utf-8") if path.exists() else "", message)
            return True
    return ensure_secrets_ignored(root, message)


def _add_env_rule(path: Path, text: str, message: str) -> None:
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n# Secrets (added by Framework)\n.env\n", encoding="utf-8")
    inform(message)


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


# What Quarto's Jupyter engine imports to run Python chunks (nbformat and nbclient,
# in Quarto's share/jupyter/jupyter.py), and the kernel it starts.
_QUARTO_PACKAGES = ("ipykernel", "nbformat", "nbclient")
# The kernel, and JupyterLab, so `uv run jupyter lab` opens the notebooks.
_JUPYTER_PACKAGES = ("ipykernel", "jupyterlab")


def _package_key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pyproject_dependencies(spec: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The dependencies a new project's pyproject.toml lists, as (name, requirement) pairs.

    AGENTS.md lists the same names, so both come from here.
    """
    packages = (spec.get("packages") or {}).get("default_packages") or {}
    dependencies = [
        ("framework-py", "framework-py"),
        *((name, _dependency(name, entry or {})) for name, entry in packages.items()),
    ]
    # A project renders and edits with its workflow's tools out of the box. The spec's
    # packages win when they name one of these, with a version for example.
    tools = [
        *(_QUARTO_PACKAGES if spec_tool_on(spec, "quarto") else ()),
        *(_JUPYTER_PACKAGES if spec_tool_on(spec, "jupyter") else ()),
    ]
    named = {_package_key(name) for name in packages}
    dependencies += [
        (name, name) for name in dict.fromkeys(tools) if _package_key(name) not in named
    ]
    return dependencies


def _pyproject(spec: Mapping[str, Any]) -> str:
    dependencies = [requirement for _, requirement in pyproject_dependencies(spec)]
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
    """Record how the project was made. Keys spec.v1 doesn't allow are left out, and a
    spec that doesn't match it otherwise is never written."""
    record = dict(spec)
    source = dict(record.get("source") or {"origin": "python"})
    source["framework_version"] = _version()
    record["source"] = source
    # Only what spec.v1 allows, so Framework Cloud accepts the spec as written
    from framework._spec_schema import ready

    record = ready(record)
    _write(root, ".framework/spec.json", json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def _git_init(root: Path) -> None:
    if shutil.which("git") is None:
        inform("  Git not found - skipping repository initialization")
        return

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )

    from framework.hooks import git_hooks_install, outer_repository

    top = outer_repository(root)
    if top is not None:
        # git init would hide the project from the repository it sits in
        inform(f"  Project is tracked by the enclosing git repository at {top}")
        return
    git("init")
    inform("  Initialized git repository")

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
    elif git("commit", "-m", "Initial commit from Framework").returncode == 0:
        inform("  Created initial commit")
    # After the initial commit, which holds only the files Framework wrote, so the hook
    # does not need a Python environment with framework-py before the project has one
    if git_hooks_install(root / "settings.yml", verbose=False):
        inform("  Installed git hooks")


def _is_temporary(root: Path) -> bool:
    temp = Path(tempfile.gettempdir()).resolve()
    resolved = root.resolve()
    return resolved.is_relative_to(temp) or str(resolved).startswith(
        ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
    )


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

    bare = not spec_core_files(spec)
    _create_directories(root, spec)
    _write_settings(root, spec)
    if not bare:
        _write_env(root, spec)
    _write(root, ".gitignore", str((spec.get("git") or {}).get("gitignore_content", "")) + "\n")
    ensure_secrets_ignored(root)
    _ignore_publish_folders(root, spec)
    if not bare and (spec.get("ai") or {}).get("enabled"):
        write_ai_files(root, dict(spec))
    if not bare:
        _write(root, "scaffold.py", _scaffold_py(spec))
    _write(root, "pyproject.toml", _pyproject(spec))
    ide = str((spec.get("scaffold") or {}).get("ide") or "").lower()
    if not bare and ide in WORKSPACE_IDES:
        _write(root, f"{kebab_case(spec['name'])}.code-workspace", _code_workspace())
    if spec["type"] == "presentation":
        _write(root, "presentation.qmd", _presentation(spec))

    # Blueprint files last, so they replace generated files of the same name.
    for relative, entry in files.items():
        content = entry["content"]
        _write(root, relative, fill_template(content, spec) if entry.get("template") else content)

    if not bare and spec_tool_on(spec, "quarto"):
        quarto_generate_all(
            root,
            spec["type"],
            spec.get("render_dirs") or {},
            spec.get("quarto") or {},
            spec.get("directories") or {},
        )
    if not bare:
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

    Args:
        layers_file: A JSON file with ``spec_version`` (which must be 1), ``name``,
            ``layers``, and optionally ``author`` and ``source``. The layers are resolved with
            ``spec_resolve()``.
        path: The project folder. Default: the current directory. In headless mode it must be
            a fresh checkout. Otherwise it must not exist.
        interactive: Create the project in regular mode, with git initialization and a
            registry entry. Nothing is prompted for either way. Default: False, which creates
            it headless.

    Returns:
        The project folder.

    Raises:
        FrameworkError: When the layers file cannot be read, has an unsupported
            ``spec_version``, or has no name, or when the project cannot be created there.
        SpecError: When the resolved spec is invalid.
    """
    source = Path(layers_file)
    try:
        envelope = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FrameworkError(f"Failed to read layers file '{source}': {error}") from error
    if not isinstance(envelope, dict):
        raise FrameworkError(f"Layers file '{source}' must hold a JSON object")
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


def _defaults_layer(settings: Mapping[str, Any], type: str) -> dict[str, Any]:
    """A spec layer from settings in the global-settings format.

    Reads ``author``, the ``defaults`` section (scaffold, AI assistants, and
    git), and the directories of this project type.
    """
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
        if isinstance(assistants, list) and assistants:
            # The list replaces the selection, as in R: assistants it leaves
            # out (Claude Code included) are turned off.
            selected = {name: True for name in assistants}
            layer["ai"]["assistants"] = {name: False for name in ASSISTANTS} | selected
        elif isinstance(assistants, dict):
            layer["ai"]["assistants"] = assistants
    git = defaults.get("git")
    if isinstance(git, dict):
        layer["git"] = {} if git.get("initialize") is None else {"use_git": git["initialize"]}
        # As in R, the user's hooks only turn checks on. Global settings hold
        # data_security: false from the shipped defaults for every type, which would
        # otherwise turn off the checks a privacy-sensitive project starts with; the
        # package defaults already have every other check off.
        hooks = git.get("hooks")
        if isinstance(hooks, dict):
            enabled = {name: True for name, value in hooks.items() if value is True}
            if enabled:
                layer["git"]["hooks"] = enabled
    types = settings.get("project_types")
    type_config = types.get(type) if isinstance(types, dict) else None
    if isinstance(type_config, dict) and isinstance(type_config.get("directories"), dict):
        layer["directories"] = type_config["directories"]
    return layer


def user_layer(type: str) -> dict[str, Any]:
    """The user's defaults from the global settings, as a spec layer.

    Reads ``author`` and the ``defaults`` section (scaffold, AI assistants,
    git, and the directories for this project type) of ``settings.yml`` in
    ``config_dir()``. A list in ``defaults.ai.assistants`` replaces the
    default selection.
    """
    return _defaults_layer(global_settings(), type)


def catalog_layer(type: str) -> dict[str, Any]:
    """The user's settings catalog, ``settings-catalog.yml`` in ``config_dir()``, as a spec layer.

    R's ``new()`` starts from the settings catalog with this file merged over
    it, so Python applies it too, below the global settings. Its keys are read
    as in the global settings. Without the file the layer is empty.
    """
    path = config_dir() / "settings-catalog.yml"
    catalog = _yaml.read(path) if path.is_file() else None
    return _defaults_layer(catalog, type) if isinstance(catalog, dict) else {}


# App project types: Framework Cloud blueprints, so they need a login. In Python,
# shiny is Shiny for Python.
APP_TYPES = {
    "shiny": "framework/shiny_python",
    "shiny_python": "framework/shiny_python",
    "fastapi": "framework/fastapi",
}
# R's app types, with the Python type to use instead.
R_APP_TYPES = {"plumber": ("Plumber", "fastapi")}


def _invalid_type() -> FrameworkError:
    return FrameworkError(
        f"Invalid project type. Must be one of: {', '.join(TYPES)}, "
        f"or, with Framework Cloud, {', '.join(APP_TYPES)}"
    )


def _app_blueprint(type: str) -> str:
    """The blueprint for an app type. It needs a Framework Cloud login, checked first."""
    from framework.cloud import CloudError, cloud_server, cloud_token, host, login_hint

    blueprint = APP_TYPES[type]
    server = cloud_server().url
    if cloud_token(server) is None:
        raise CloudError(
            f'type="{type}" builds the Framework Cloud blueprint {blueprint}. '
            f"No cloud token found for {host(server)}. {login_hint(server)}"
        )
    return blueprint


def new_project(
    name: str | None = None,
    location: str | Path | None = None,
    type: str | None = None,
    *,
    workflow: str | None = None,
    tools: Mapping[str, bool] | Sequence[str] | None = None,
    package_sets: Sequence[str] = (),
    skill_sets: Sequence[str] = (),
    blueprint: str | None = None,
    structure: str | None = None,
    skills: Sequence[str] = (),
    sensitive: bool | None = None,
    ai: bool | Mapping[str, Any] | None = None,
    git: bool | None = None,
    key: str | None = None,
    url: str | None = None,
    interactive: bool | None = None,
    offline: bool = False,
) -> Path:
    """Create a new Python project with your defaults.

    Args:
        name: Project name. Asked for when interactive.
        location: The project folder to create. Asked for when interactive.
        type: project, project_sensitive, course, presentation, or bare. App types
            ``"shiny"`` (Shiny for Python; ``"shiny_python"`` is the same) and
            ``"fastapi"`` are Framework Cloud blueprints: ``type="fastapi"`` is
            ``blueprint="framework/fastapi"``, and needs
            [`cloud_login()`](ref:cloud_login). ``"plumber"`` is R only. When
            you are logged in to Framework Cloud, every choice is resolved there:
            a type is its system blueprint (``framework/<type>``), and with no
            choices at all the project starts from your defaults. When not logged
            in, a missing type is asked for interactively and is ``project``,
            the Standard Project, otherwise, as in R's ``new()``. Pass
            ``type="bare"`` for a bare project.
        workflow: How notebooks are written: ``"jupyter"`` (``.ipynb``, with Quarto kept
            for rendering and publishing), ``"quarto"`` (``.qmd``), or ``"scripts"`` (no
            notebooks). Default: ``"jupyter"`` for project and project_sensitive, and the
            structure's own setup for the others (Quarto for course and presentation).
        tools: Tools to turn on, as a list of ids such as ``["jupyter"]``, or a map of id to
            true or false, which can also turn off a default such as Quarto. A tool another
            one needs is turned on too, with a message. Default: the type's tools (uv, and
            Quarto for every type but bare). See [`tools_available()`](ref:tools_available).
        package_sets: Package sets to add. Offline, a bundled set; with ``blueprint`` or
            ``structure``, any handle on Framework Cloud.
        skill_sets: Skill sets to add, such as ``["framework"]``. They replace the type's
            default skills; a sensitive project keeps framework-sensitive-data.
        blueprint: A Framework Cloud blueprint handle, such as ``"framework/project"`` or
            ``"ada/lab"``. With ``blueprint`` or ``structure``, the choices are resolved by the
            cloud (you must be logged in with [`cloud_login()`](ref:cloud_login)), the project
            is built from the layers it returns, and then it is registered there and gets a
            project token in ``.env``. Cannot be combined with ``type``.
        structure: A Framework Cloud structure handle, such as ``"framework/course"``. It
            replaces the blueprint's structure.
        skills: Skill handles to add on Framework Cloud. Needs ``blueprint`` or
            ``structure``.
        sensitive: Turn the sensitive-data setting on or off. Needs ``blueprint`` or
            ``structure``; offline, use ``type="project_sensitive"``.
        ai: Turn AI assistants on or off, or a map such as
            ``{"enabled": True, "assistants": {"claude": True}}``. Needs ``blueprint`` or
            ``structure``.
        git: Turn git on or off. Needs ``blueprint`` or ``structure``.
        key: A Framework Cloud project token. The project's definition (its
            layers) is fetched from the cloud, and the token is saved in the
            new project's ``.env`` for publishing and the ledger.
        url: With ``key``: the Framework Cloud server that issued the token. No login is
            needed. The server is saved as ``FW_CLOUD_URL`` in the project's ``.env``.
            Default: ``FW_CLOUD_URL``, then the server you last logged in to with
            [`cloud_login()`](ref:cloud_login), then https://framework.pub.
        interactive: Whether to prompt. Default: whether stdin is a terminal.
        offline: Build from Framework's built-in defaults without contacting Framework Cloud.
            When you are logged in and pass no choices, ``new()`` resolves your defaults on
            the cloud and caches them. If the cloud can't be reached, it builds from that
            cache and says so. With no cache it asks first, or, when not interactive, stops
            unless ``offline`` is True. It never falls back silently.

    Returns:
        The project folder.
    """
    # Before anything can inform(), so the first call in a process shows every message
    show_messages()
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if key is not None:
        return _new_from_cloud(key, location, interactive, url)
    if url is not None:
        raise FrameworkError("url names the server for key. Pass key too.")
    cloud_choices = blueprint is not None or structure is not None
    if cloud_choices and type is not None:
        raise FrameworkError("Pass type, or blueprint and structure, not both")
    if type in R_APP_TYPES:
        label, instead = R_APP_TYPES[type]
        raise FrameworkError(
            f'{label} is an R project type. For a Python API, use type="{instead}".'
        )
    if type in APP_TYPES:
        # Exactly blueprint="framework/<app>"
        blueprint, type, cloud_choices = _app_blueprint(type), None, True
    if not cloud_choices:
        needs_cloud = [
            name
            for name, given in (
                ("skills", bool(skills)),
                ("sensitive", sensitive is not None),
                ("ai", ai is not None),
                ("git", git is not None),
            )
            if given
        ]
        if needs_cloud:
            raise FrameworkError(
                f"{', '.join(needs_cloud)} need a Framework Cloud blueprint or structure. "
                "Pass blueprint= or structure=, or use type= for Framework's own structures."
            )
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

    def local(register: bool = True) -> Path:
        return _new_local(
            name, location, type, workflow, tools, package_sets, skill_sets, interactive, register
        )

    if cloud_choices:
        body = _choices_body(
            name,
            blueprint,
            structure,
            workflow,
            tools,
            package_sets,
            skill_sets,
            skills,
            sensitive,
            ai,
            git,
        )
        return _new_from_choices(name, location, body)
    if offline:
        return local(register=False)
    signed_in = _signed_in()
    if signed_in is None:
        return local()
    # Logged in: the cloud resolves every choice. A Framework type is its system blueprint.
    if type is not None and type not in TYPES:
        raise _invalid_type()
    body = _choices_body(
        name,
        f"framework/{type}" if type else None,
        None,
        workflow,
        tools,
        package_sets,
        skill_sets,
        (),
        None,
        None,
        None,
    )
    return _resolve_and_build(name, location, body, *signed_in, interactive, local)


def _new_local(
    name: str,
    location: str | Path,
    type: str | None,
    workflow: str | None,
    tools: Mapping[str, bool] | Sequence[str] | None,
    package_sets: Sequence[str],
    skill_sets: Sequence[str],
    interactive: bool,
    register: bool = True,
) -> Path:
    """Build from Framework's built-in defaults and your local settings."""
    if type is None:
        if interactive:
            choices = ", ".join(TYPES)
            type = input(f"Project type ({choices}) [project]: ").strip() or "project"
        else:
            type = "project"
    if type not in TYPES:
        raise _invalid_type()

    from framework.features import definition_layers, workflow_layers

    if workflow is None and type in DEFAULT_JUPYTER:
        workflow = "jupyter"
    layers: Sequence[Any] = [
        # The workflow first, so tools chosen here win
        *(workflow_layers(workflow) if workflow else []),
        *definition_layers(tools or (), package_sets, skill_sets),
        {"kind": "user", "key": "settings-catalog", "layer": catalog_layer(type)},
        {"kind": "user", "layer": user_layer(type)},
        {"kind": "project", "layer": {"type": type, "language": "python"}},
    ]
    spec = spec_resolve(layers, name).spec
    if skill_sets:
        _replace_default_skills(spec, type, layers)
    spec["source"] = {"origin": "python"}
    inform(f"Creating {type} project: {name}")
    root = project_create(spec, location)
    if register and type != "bare":
        from framework.cloud import register_project

        register_project(root, name, type, spec, package_sets=package_sets, skill_sets=skill_sets)
    return root


def _signed_in() -> tuple[str, str] | None:
    """The cloud server and your token, or None when you are not logged in."""
    from framework.cloud import CloudError, cloud_server, cloud_token

    try:
        server = cloud_server().url
    except CloudError:
        return None
    token = cloud_token(server)
    return None if token is None else (server, token)


def _resolve_and_build(
    name: str,
    location: str | Path,
    body: dict[str, Any],
    server: str,
    token: str,
    interactive: bool,
    local: Callable[[], Path],
) -> Path:
    """Resolve the choices on the cloud and build the project.

    With no choices, the result is your defaults, which are cached for offline
    use. Offline, the cache is used for those. Anything else asks, or stops
    when not interactive, before building from the built-ins.
    """
    from framework._defaults_cache import read_cached_defaults, write_cached_defaults
    from framework.cloud import CloudOfflineError, _MissingEndpointError, host, resolve_choices

    if Path(location).expanduser().exists():
        raise FrameworkError(f"Project directory already exists: {Path(location).expanduser()}")
    defaults_only = set(body) <= {"name", "language"}
    try:
        envelope, revision = resolve_choices(body, token, server)
    except _MissingEndpointError:
        problem = (
            f"{host(server)} doesn't resolve projects yet, so this project would use "
            "Framework's built-in defaults."
        )
        _confirm_built_ins(problem, interactive)
        return local()
    except CloudOfflineError as error:
        cached = read_cached_defaults(server, "python") if defaults_only else None
        if cached is None:
            what = (
                "your Framework Cloud defaults aren't cached yet"
                if defaults_only
                else "these choices need Framework Cloud to resolve them"
            )
            _confirm_built_ins(f"Can't reach {host(server)}, and {what}.\n{error}", interactive)
            return local()
        inform(f"Using your Framework Cloud defaults from {str(cached['fetched_at'])[:10]}")
        envelope = cached["envelope"]
    else:
        if defaults_only:
            inform(f"Starting from your defaults on {host(server)}")
            write_cached_defaults(server, "python", envelope, revision)
    return _build_and_register(name, location, envelope, body, server, token)


def _confirm_built_ins(problem: str, interactive: bool) -> None:
    """Ask before building from the built-ins instead of what the cloud would resolve.

    Raises:
        FrameworkError: When the answer is no, or when not interactive.
    """
    if not interactive:
        raise FrameworkError(
            f"{problem}\nPass offline=True to build from Framework's built-in defaults instead."
        )
    print(problem, file=sys.stderr)
    answer = input("Build from Framework's built-in defaults instead? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise FrameworkError("Cancelled. Nothing was created.")
    inform("Building from Framework's built-in defaults")


def _choices_body(
    name: str,
    blueprint: str | None,
    structure: str | None,
    workflow: str | None,
    tools: Mapping[str, bool] | Sequence[str] | None,
    package_sets: Sequence[str],
    skill_sets: Sequence[str],
    skills: Sequence[str],
    sensitive: bool | None,
    ai: bool | Mapping[str, Any] | None,
    git: bool | None,
) -> dict[str, Any]:
    """The body for the cloud's resolve and create endpoints.

    Framework's tools get what they require added here, with a message, so
    the cloud's strict check never rejects the request.
    """
    from framework.features import _tools, resolve_tools

    choices = dict(tools) if isinstance(tools, Mapping) else dict.fromkeys(tools or (), True)
    off = [t for t, on in choices.items() if on is False]
    known = [t for t, on in choices.items() if on and t in _tools()]
    for tool in resolve_tools(known, off):
        choices.setdefault(tool, True)
    body: dict[str, Any] = {"name": name, "language": "python"}
    if blueprint is not None:
        body["blueprint"] = blueprint
    if structure is not None:
        body["structure"] = structure
    if workflow is not None:
        body["workflow"] = workflow
    if choices:
        body["tools"] = choices
    for field, values in (
        ("package_sets", package_sets),
        ("skill_sets", skill_sets),
        ("skills", skills),
    ):
        if values:
            # A bare id names one of Framework's own, as offline
            body[field] = [v if "/" in v or field == "skills" else f"framework/{v}" for v in values]
    settings = {
        k: v for k, v in (("sensitive", sensitive), ("git", git), ("ai", ai)) if v is not None
    }
    if settings:
        body["settings"] = settings
    return body


def _new_from_choices(name: str, location: str | Path, body: dict[str, Any]) -> Path:
    """Resolve choices on the cloud, build the project, then register it there."""
    from framework.cloud import _require_token, cloud_server, host, resolve_choices

    server = cloud_server().url
    token = _require_token(server)
    if Path(location).expanduser().exists():
        raise FrameworkError(f"Project directory already exists: {Path(location).expanduser()}")
    inform(f"Resolving the project's pieces on {host(server)}")
    envelope, _ = resolve_choices(body, token, server)
    return _build_and_register(name, location, envelope, body, server, token)


def _build_and_register(
    name: str,
    location: str | Path,
    envelope: Mapping[str, Any],
    body: Mapping[str, Any],
    server: str,
    token: str,
) -> Path:
    """Build from a resolved envelope, then register the project on the cloud."""
    from framework.cloud import CloudError, api, host, write_project_token

    spec = spec_resolve(envelope.get("layers") or [], name, envelope.get("author")).spec
    spec["source"] = envelope.get("source") or {"origin": "cloud"}
    root = project_create(spec, location)
    if spec.get("type") == "bare":
        # As offline: a bare project has no .env for a project token, so it stays local
        return root
    try:
        created = api("/api/v1/projects", token, server, method="POST", body=body)
    except CloudError as error:
        inform(f"Cloud project registration skipped: {error}")
        return root
    inform(f"[ok] Registered on {host(server)}: {created.get('slug')}")
    if spec_core_files(spec) and created.get("token"):
        write_project_token(root, str(created["token"]), server)
    return root


def _replace_default_skills(spec: dict[str, Any], type: str, layers: Sequence[Any]) -> None:
    """Chosen skill sets replace the type's default skills, as in R.

    A sensitive project keeps framework-sensitive-data.
    """
    from framework.spec.resolve import package_defaults

    chosen = {
        name
        for entry in layers
        if str(entry.get("key", "")).startswith("framework/skills-")
        for name in (entry["layer"].get("skills") or {})
    }
    defaults = set(package_defaults(type)["skills"]) - {"framework-sensitive-data"}
    spec["skills"] = {
        name: value
        for name, value in spec["skills"].items()
        if name not in defaults or name in chosen
    }


def _new_from_cloud(
    key: str, location: str | Path | None, interactive: bool, url: str | None = None
) -> Path:
    from framework.cloud import fetch_layers, write_project_token

    envelope, server = fetch_layers(key, url)
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
    if spec_core_files(spec):
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

    root = Path(project_path).expanduser()
    if not is_safe_path(path):
        raise FrameworkError(f"Path must be relative and stay inside the project: {path}")
    settings_file = next(
        (root / name for name in ("settings.yml", "config.yml") if (root / name).is_file()), None
    )
    if settings_file is None:
        raise FrameworkError("Project config not found (checked settings.yml and config.yml)")
    folder = root / path
    folder.mkdir(parents=True, exist_ok=True)

    def add(raw: Any) -> Any:
        raw = raw if isinstance(raw, dict) else {}
        config = raw["default"] if isinstance(raw.get("default"), dict) else raw
        extra = config.get("extra_directories")
        if not isinstance(extra, list):
            extra = config["extra_directories"] = []
        # Edited in place, so the file keeps its comments
        for index in reversed(range(len(extra))):
            if isinstance(extra[index], dict) and extra[index].get("key") == key:
                del extra[index]
        extra.append({"key": key, "label": label, "path": path})
        return raw

    _yaml.update(settings_file, add)
    return folder


# Backward-compatible project creation alias.
new = new_project
