"""Global settings, the settings catalog, and user templates.

The global settings live in ``settings.yml`` in ``config_dir()``. Python
keeps its own config folder, separate from R's; ``config_sync()`` copies R's
settings over.
"""

from __future__ import annotations

import copy
from importlib.resources import files
from pathlib import Path
from typing import Any

from framework import _yaml
from framework._log import inform, show_messages
from framework.config_home import (
    _global_settings_path,
    _save_global_settings,
    config_dir,
    global_settings,
)
from framework.errors import FrameworkError, SettingsError
from framework.settings import modify_list
from framework.spec.check import secret_env_values
from framework.spec.resolve import TYPES

# Settings ---------------------------------------------------------------------

# Sections a new value replaces instead of merging into, as in R.
_REPLACED = (("defaults", "packages"), ("defaults", "connections"), ("defaults", "env"))
_TYPE_REPLACED = ("directories", "extra_directories", "directories_enabled")


def _validate(settings: dict[str, Any]) -> None:
    author = settings.get("author")
    if author is not None:
        if not isinstance(author, dict):
            raise SettingsError("author must be a mapping of name, email, and affiliation")
        for key in ("name", "email", "affiliation", "orcid", "url"):
            if author.get(key) is not None and not isinstance(author[key], str):
                raise SettingsError(f"author.{key} must be text")
    for key in ("defaults", "project_types", "git", "privacy", "global"):
        if settings.get(key) is not None and not isinstance(settings[key], dict):
            raise SettingsError(f"{key} must be a mapping")
    for root in (
        settings.get("projects_root"),
        (settings.get("global") or {}).get("projects_root"),
    ):
        if root is not None and not isinstance(root, str):
            raise SettingsError("projects_root must be a folder path")
    types = settings.get("project_types") or {}
    unknown = sorted(set(types) - set(TYPES))
    if unknown:
        raise SettingsError(f"Unknown project types: {', '.join(unknown)}")
    # R stores defaults.env as .env text, or as {raw, variables}. A plain
    # mapping of names to values is read as the variables.
    env = (settings.get("defaults") or {}).get("env")
    raw: Any
    variables: Any
    if isinstance(env, str):
        raw, variables = env, None
    elif isinstance(env, dict) and env and set(env) <= {"raw", "variables"}:
        raw, variables = env.get("raw"), env.get("variables")
    elif isinstance(env, dict) or env is None:
        raw, variables = None, env
    else:
        raise SettingsError("defaults.env must be .env text or a mapping")
    secrets = secret_env_values(raw if isinstance(raw, str) else None, variables)
    if secrets:
        raise SettingsError(
            f"defaults.env: secrets must not be stored in settings ({', '.join(secrets)}). "
            "Leave the values empty and put them in each project's .env file."
        )


def _tilde(path: str) -> str:
    expanded = Path(path).expanduser()
    home = Path.home()
    try:
        return "~/" + expanded.relative_to(home).as_posix()
    except ValueError:
        return path


def configure_global(
    settings: dict[str, Any] | None = None, *, validate: bool = True
) -> dict[str, Any]:
    """Read or update the global settings.

    Args:
        settings: Values to merge into the global settings, such as
            ``{"author": {"name": "Ada Lovelace"}}``. Nested mappings merge,
            except ``defaults.packages``, ``defaults.connections``,
            ``defaults.env``, and each project type's directories, which are
            replaced. Without ``settings``, nothing changes.
        validate: Check the values before saving. Secrets in
            ``defaults.env`` are refused.

    Returns:
        The global settings after the update.
    """
    current = global_settings()
    if settings is None:
        return current
    if not isinstance(settings, dict):
        raise TypeError("settings must be a dict")
    updated: dict[str, Any] = modify_list(copy.deepcopy(current), copy.deepcopy(settings))
    for section, key in _REPLACED:
        value = (settings.get(section) or {}).get(key)
        if value is not None:
            updated.setdefault(section, {})[key] = copy.deepcopy(value)
    for type_name, type_settings in (settings.get("project_types") or {}).items():
        for key in _TYPE_REPLACED:
            value = (type_settings or {}).get(key)
            if value is None:
                continue
            if key == "directories" and isinstance(value, dict):
                value = {k: v for k, v in value.items() if v}
            updated["project_types"][type_name][key] = copy.deepcopy(value)
    if validate:
        _validate(updated)
    for holder in (updated, updated.get("global") or {}):
        if "projects_root" in holder:
            if holder["projects_root"]:
                holder["projects_root"] = _tilde(holder["projects_root"])
            else:
                del holder["projects_root"]
    _save_global_settings(updated)
    show_messages()
    inform(f"✓ Global settings updated in {_global_settings_path()}")
    return updated


# The settings catalog ---------------------------------------------------------

_GITIGNORE = {
    "project": "project",
    "project_sensitive": "sensitive",
    "course": "course",
    "presentation": "presentation",
}


def load_settings_catalog(include_user: bool = True, validate: bool = True) -> dict[str, Any]:
    """The defaults new Python projects start from.

    The catalog has ``defaults`` (Framework's package defaults, layer 0 of
    ``spec_resolve()``) and ``project_types``, each with its ``.gitignore``
    text. With ``include_user``, ``settings-catalog.yml`` in ``config_dir()``
    is merged over it.
    """
    shipped = files("framework").joinpath("templates")
    defaults = _yaml.load(shipped.joinpath("defaults.yml").read_text(encoding="utf-8")) or {}
    catalog: dict[str, Any] = {
        "meta": {"version": 2, "language": "python"},
        "defaults": defaults,
        "project_types": {
            name: {
                "gitignore": shipped.joinpath(f"gitignore/{_GITIGNORE[name]}").read_text(
                    encoding="utf-8"
                )
            }
            for name in _GITIGNORE
        },
    }
    user_path = config_dir() / "settings-catalog.yml"
    if include_user and user_path.is_file():
        user = _yaml.read(user_path)
        if isinstance(user, dict):
            catalog = modify_list(catalog, user)
    if validate:
        for key in ("defaults", "project_types"):
            if not isinstance(catalog.get(key), dict):
                raise SettingsError(f"Settings catalog is missing '{key}'")
    return catalog


# User templates ---------------------------------------------------------------

# Template names and the stub files they replace. make_notebook(),
# make_script(), and make_presentation() use a user template when there is
# no project stub of the same name.
TEMPLATES = {
    "notebook": "notebook-default.qmd",
    "script": "script-default.py",
    "presentation": "notebook-revealjs.qmd",
}


def templates_dir() -> Path:
    """Where your templates are kept: ``templates/`` in ``config_dir()``."""
    return config_dir() / "templates"


def _template_file(name: str) -> str:
    if name not in TEMPLATES:
        hint = (
            " .gitignore content comes from the project type and from layers; "
            "add rules with a facet."
            if name.startswith("gitignore")
            else ""
        )
        raise FrameworkError(
            f"Unknown template: {name}. Templates are {', '.join(TEMPLATES)}.{hint}"
        )
    return TEMPLATES[name]


def framework_template_path(name: str) -> Path:
    """The file your version of a template is saved in (it may not exist yet)."""
    return templates_dir() / _template_file(name)


def read_framework_template(name: str) -> str:
    """A template's text: yours when you saved one, otherwise Framework's."""
    path = framework_template_path(name)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    shipped = files("framework").joinpath(f"templates/stubs/{_template_file(name)}")
    return shipped.read_text(encoding="utf-8")


def write_framework_template(name: str, contents: str) -> Path:
    """Save your version of a template. Returns its path."""
    if not isinstance(contents, str):
        raise TypeError("contents must be text")
    path = framework_template_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents if contents.endswith("\n") else contents + "\n", encoding="utf-8")
    return path


def reset_framework_template(name: str) -> Path:
    """Remove your version of a template, so Framework's is used again."""
    path = framework_template_path(name)
    path.unlink(missing_ok=True)
    return path
