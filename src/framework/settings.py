"""Reading and writing project settings (settings.yml and its split files).

This follows ``settings_read()`` in the R package so that one settings.yml
gives the same values in both languages:

- A file with a ``default:`` section (or another common environment name at
  the top level) has environment sections. The active environment is merged
  over ``default``: mappings merge by key, and anything else is replaced.
- The project's ``.env`` is read before split files. Its values are visible only while
  the settings resolve, shell values win, and empty values are skipped.
- A top-level value ending in ``.yml`` or ``.yaml`` names a split file, which
  is read, merged in, and may itself name split files.
- ``env("VAR")``, ``env("VAR", "default")``, and ``!expr Sys.getenv(...)``
  are replaced with environment variable values. Any other ``!expr`` is R code,
  which Python cannot run, so it raises an error.
"""

from __future__ import annotations

import os
import re
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from framework import _yaml, dotenv
from framework.errors import FrameworkWarning, SettingsError
from framework.project import find_settings_file

_ENV_KEYWORDS = frozenset({"default", "production", "development", "test", "testing", "staging"})
_STANDARD_SECTIONS = ("data", "connections", "git", "security", "packages", "directories")
_SPLIT_FILE = re.compile(r"\.ya?ml$", re.IGNORECASE)
_ENV_CALL = re.compile(r'^env\("(.*?)"(?:,\s*"(.*?)")?\)$')
_EXPR = re.compile(r"^!expr\s+")
_SYS_GETENV = re.compile(
    r"""^Sys\.getenv\(\s*(['"])(.*?)\1\s*(?:,\s*(?:unset\s*=\s*)?(['"])(.*?)\3\s*)?\)$"""
)

_MISSING = object()


class SettingsWarning(FrameworkWarning):
    """A settings file has a conflict that Framework resolved."""


def active_environment() -> str:
    """The settings environment in use.

    ``FW_CONFIG_ACTIVE``, then R's ``R_CONFIG_ACTIVE`` and ``R_CONFIG_NAME``,
    then ``"default"``.
    """
    for name in ("FW_CONFIG_ACTIVE", "R_CONFIG_ACTIVE", "R_CONFIG_NAME"):
        found = os.environ.get(name)
        if found:
            return found
    return "default"


def _settings_path(settings_file: str | Path | None) -> Path:
    if settings_file is None:
        found = find_settings_file()
        if found is None:
            raise SettingsError("No settings file found. Looking for settings.yml or config.yml")
        return found
    path = Path(settings_file).expanduser()
    if not path.is_file():
        raise SettingsError(f"Settings file not found: {path}")
    return path


# R semantics ----------------------------------------------------------------


def _r_assign(target: dict[str, Any], key: str, value: Any) -> None:
    """``target[[key]] <- value`` in R: assigning NULL removes the key."""
    if value is None:
        target.pop(key, None)
    else:
        target[key] = value


def _r_type(value: Any) -> type | None:
    """The R atomic type a YAML scalar becomes, or None for anything else."""
    if isinstance(value, bool | int | float | str):
        return type(value)
    return None


def _is_r_list(value: Any) -> bool:
    """Whether R's yaml package reads this value as a list.

    Mappings are lists. A sequence is an atomic vector when it is non-empty
    and every element is a scalar of the same type, and a list otherwise.
    """
    if isinstance(value, dict):
        return True
    if not isinstance(value, list):
        return False
    types = {_r_type(item) for item in value}
    return not value or None in types or len(types) > 1


def modify_list(base: Any, update: Any) -> Any:
    """R's ``utils::modifyList()``.

    Named entries of ``update`` replace those in ``base``, recursing when both
    values are R lists, and a NULL removes the entry. A sequence of scalars is
    an R vector, so it replaces the old value whole. A sequence of mappings is
    an unnamed R list, which changes nothing when merged into another list.
    """
    if not isinstance(update, dict):
        return base
    if not isinstance(base, dict):
        # An unnamed list gains the named entries, as in R.
        base = {} if not base else {str(i + 1): item for i, item in enumerate(base)}
    result = dict(base)
    for key, value in update.items():
        if key in result and _is_r_list(result[key]) and _is_r_list(value):
            result[key] = modify_list(result[key], value)
        else:
            _r_assign(result, key, value)
    return result


def _modify_in_place(base: dict[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    """``modify_list()`` that changes ``base`` itself, so a file read for editing keeps
    the comments on the entries it does not replace."""
    for key, value in update.items():
        current = base.get(key)
        if key in base and isinstance(current, dict) and isinstance(value, dict):
            _modify_in_place(current, value)
        elif key in base and _is_r_list(current) and _is_r_list(value):
            base[key] = modify_list(current, value)
        else:
            _r_assign(base, key, value)
    return base


def merge_env(base: Any, override: Any) -> Any:
    """Merge an environment section over ``default``, as R's ``.fw_merge_env()`` does.

    Mappings merge by key, recursively, and a null value removes the key.
    Anything else, including every sequence, replaces the old value whole.
    """
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    result = dict(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_env(result[key], value)
        else:
            result[key] = value
    return result


def _has_environment_sections(config: Any) -> bool:
    if not isinstance(config, dict) or not config:
        return False
    return any(key in _ENV_KEYWORDS for key in config)


def _select_environment(raw: Any, environment: str, label: str, *, warn: bool) -> Any:
    if not _has_environment_sections(raw):
        return raw
    if "default" not in raw:
        raise SettingsError(f"{label} has environment sections but no 'default' environment")
    config = raw["default"]
    if environment != "default":
        if environment in raw:
            config = merge_env(config, raw[environment])
        elif warn:
            warnings.warn(
                f"Environment '{environment}' not found in config, using 'default'",
                SettingsWarning,
                stacklevel=4,
            )
    return config


# Expressions ----------------------------------------------------------------


def _check_publish_credentials(publish: Any) -> None:
    """Named publishing destinations keep credential values in the environment."""
    if not isinstance(publish, dict):
        return
    destinations = publish.get("destinations")
    if not isinstance(destinations, dict):
        return
    credential_keys = {
        "access_key",
        "access_key_id",
        "secret_key",
        "secret_access_key",
        "session_token",
        "password",
        "token",
        "api_key",
    }
    for name, options in destinations.items():
        if not isinstance(options, dict):
            continue
        for key, raw in options.items():
            if str(key).lower() not in credential_keys or raw in (None, ""):
                continue
            call = _ENV_CALL.match(raw) if isinstance(raw, str) else None
            if call is not None and not call.group(2):
                continue
            expression = _EXPR.sub("", raw, count=1).strip() if isinstance(raw, str) else ""
            system = _SYS_GETENV.match(expression)
            if system is not None and not system.group(4):
                continue
            raise SettingsError(
                f"publish.destinations.{name}.{key} must use an environment reference "
                'such as env("VARIABLE").'
            )


def _evaluate(value: Any) -> Any:
    if isinstance(value, dict):
        if "publish" in value:
            _check_publish_credentials(value["publish"])
        return {key: _evaluate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_evaluate(item) for item in value]
    if not isinstance(value, str):
        return value

    if _EXPR.match(value):
        code = _EXPR.sub("", value, count=1).strip()
        call = _SYS_GETENV.match(code)
        if call is None:
            raise SettingsError(
                f"Cannot evaluate '!expr {code}' in Python. Settings shared with Python can use "
                'env("VAR") or env("VAR", "default"), or !expr Sys.getenv("VAR").'
            )
        return os.environ.get(call.group(2), call.group(4) or "")

    call = _ENV_CALL.match(value)
    if call is not None:
        return os.environ.get(call.group(1), call.group(2) or "")
    return value


# Split files ----------------------------------------------------------------


def _is_file_reference(value: Any) -> bool:
    return isinstance(value, str) and bool(_SPLIT_FILE.search(value))


def _resolve_split_files(
    config: Any,
    environment: str,
    parent: Path,
    visited: tuple[Path, ...],
    config_root: Path,
    main_keys: frozenset[str],
) -> Any:
    if not isinstance(config, dict):
        return config

    file_refs: set[str] = set()
    for key in list(config):
        value = config.get(key, _MISSING)
        if not _is_file_reference(value):
            continue
        file_refs.add(key)

        reference = str(value)
        candidate = Path(reference)
        split_path = (candidate if candidate.is_absolute() else config_root / candidate).resolve()
        if not split_path.is_file():
            raise SettingsError(f"{reference} not found (referenced from {parent.name})")
        if split_path in visited:
            chain = " -> ".join(str(p) for p in (*visited, split_path))
            raise SettingsError(f"Circular reference detected: {chain}")

        try:
            raw = _yaml.read(split_path)
        except Exception as error:
            raise SettingsError(f"Failed to parse split file '{reference}': {error}") from error

        split = _select_environment(raw, environment, f"Split file '{reference}'", warn=False)
        split = _resolve_split_files(
            split, environment, split_path, (*visited, split_path), config_root, main_keys
        )
        # Publish credentials are validated while still expressed as env references.
        # Keep that section raw until the final whole-settings evaluation.
        raw_publish = split.get("publish", _MISSING) if isinstance(split, dict) else _MISSING
        if raw_publish is not _MISSING:
            split = _evaluate({k: v for k, v in split.items() if k != "publish"})
            split["publish"] = raw_publish
        else:
            split = _evaluate(split)
        if not isinstance(split, dict):
            continue

        for split_key, split_value in split.items():
            if split_key == key:
                _r_assign(config, split_key, split_value)
            elif split_key in config:
                if split_key in file_refs:
                    continue
                if _is_file_reference(config[split_key]):
                    # A file reference processed later, not a conflict.
                    _r_assign(config, split_key, split_value)
                elif split_key == "options":
                    if isinstance(config["options"], dict) and isinstance(split_value, dict):
                        config["options"] = modify_list(split_value, config["options"])
                elif split_key in main_keys:
                    warnings.warn(
                        f"Key '{split_key}' defined in both main config and '{reference}'. "
                        "Using value from main config.",
                        SettingsWarning,
                        stacklevel=4,
                    )
                else:
                    warnings.warn(
                        f"Key '{split_key}' already defined, ignoring value from '{reference}'",
                        SettingsWarning,
                        stacklevel=4,
                    )
            else:
                _r_assign(config, split_key, split_value)
    return config


_CONNECTION_KEYS = ("databases", "storage_buckets", "default_database", "default_storage_bucket")


def _lift_connections(config: dict[str, Any]) -> dict[str, Any]:
    """Move connection keys that landed at the top level under ``connections``.

    Projects created by R 2.0 before a fix wrote ``settings/connections.yml``
    without its ``connections:`` wrapper, so its keys merged into the top
    level. R's ``settings_read()`` moves them back, and so does Python. A
    value already under ``connections`` wins.
    """
    stray = [key for key in _CONNECTION_KEYS if key in config]
    if not stray:
        return config
    connections = config.get("connections")
    if not isinstance(connections, dict):
        connections = {}
    for key in stray:
        current = connections.get(key)
        if current is None or (isinstance(current, dict | list) and not current):
            connections[key] = config[key]
        del config[key]
    config["connections"] = connections
    return config


def _dotenv_file(config: Mapping[str, Any], root: Path) -> Path:
    """The project's ``.env``: ``dotenv_location`` when set, else the root's."""
    location = config.get("dotenv_location")
    if location is None:
        return root / ".env"
    env_path = Path(str(location))
    if not env_path.is_absolute():
        env_path = root / env_path
    return env_path / ".env" if env_path.is_dir() else env_path


def project_dotenv() -> dict[str, str]:
    """The current project's ``.env`` values, read without touching ``os.environ``.

    For values used after ``settings_read()`` returns, such as storage credentials. Empty
    outside a project or when the file cannot be read.
    """
    path = find_settings_file()
    if path is None:
        return {}
    try:
        raw = _yaml.read(path)
        config = _select_environment(raw, active_environment(), str(path), warn=False)
        return dotenv.read(_dotenv_file(config if isinstance(config, dict) else {}, path.parent))
    except Exception:
        return {}


# Public API -----------------------------------------------------------------


def settings_read(
    settings_file: str | Path | None = None, environment: str | None = None
) -> dict[str, Any]:
    """Read the project settings, with environment sections and split files resolved.

    Args:
        settings_file: The settings file. Default: settings.yml (or config.yml)
            in the current folder or the nearest parent folder that has one.
        environment: The environment section to merge over ``default``.
            Default: ``active_environment()``.

    Returns:
        The settings as a dict. The ``data``, ``connections``, ``git``,
        ``security``, ``packages``, ``directories``, and ``options`` sections
        are always present.
    """
    path = _settings_path(settings_file)
    active = environment or active_environment()

    try:
        raw = _yaml.read(path)
    except Exception as error:
        raise SettingsError(f"Failed to parse settings file '{path}': {error}") from error

    config = _select_environment(raw, active, f"Settings file '{path}'", warn=True)
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise SettingsError(f"Settings file '{path}' must contain a mapping")

    env_path = _dotenv_file(config, path.parent)
    values: dict[str, str] = {}
    if env_path.is_file():
        try:
            values = dotenv.read(env_path)
        except SettingsError as error:
            warnings.warn(f"Failed to load .env file: {error}", SettingsWarning, stacklevel=2)

    # The .env values are visible only while env() and !expr values resolve. Shell values
    # win, empty values are skipped, and nothing stays in os.environ afterward.
    with dotenv.scoped(values):
        resolved_path = path.resolve()
        config = _resolve_split_files(
            config, active, resolved_path, (), resolved_path.parent, frozenset(config)
        )
        config = _lift_connections(config)

        for section in (*_STANDARD_SECTIONS, "options"):
            if config.get(section) is None:
                config[section] = {}

        evaluated: dict[str, Any] = _evaluate(config)
    return evaluated


def settings(
    key: str | None = None, default: Any = None, settings_file: str | Path | None = None
) -> Any:
    """A settings value by dot-notation key, such as ``"directories.notebooks"``.

    A one-part key is looked up in ``directories`` first, so
    ``settings("notebooks")`` returns the notebooks folder. With no key, the
    whole settings dict is returned. A missing key returns ``default``.

    Args:
        key: A dot-notation key. A one-part key is looked up in ``directories``, then as
            ``<key>_dir`` in ``options``, then at the top level. Default: the whole settings
            dict.
        default: The value returned when the key is missing.
        settings_file: The settings file. Default: settings.yml (or config.yml) in the current
            folder or the nearest parent folder that has one.

    Returns:
        The value, the whole settings dict, or ``default``.

    Raises:
        SettingsError: When the settings file is not found or cannot be parsed.
    """
    config = settings_read(settings_file)
    if key is None:
        return config

    parts = key.split(".")
    if len(parts) == 1:
        directories = config.get("directories")
        if isinstance(directories, dict) and directories.get(key) is not None:
            return directories[key]
        options = config.get("options")
        if isinstance(options, dict) and options.get(f"{key}_dir") is not None:
            return options[f"{key}_dir"]

    value: Any = config
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return default
    return value


def _split_values(section: str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Section values wrapped in the section key, as split files hold them.

    A split file wraps its content in the section key (``git:`` in
    ``settings/git.yml``). Its other top-level keys are read as top-level
    settings, so values without the wrapper must not land there. Values may
    come with or without the wrapper. R merges them as given.
    """
    wrapped = set(values) == {section} and isinstance(values[section], Mapping)
    return {section: dict(values[section]) if wrapped else dict(values)}


def settings_write(
    values: Mapping[str, Any],
    settings_file: str | Path | None = None,
    section: str | None = None,
) -> None:
    """Write project settings.

    With ``section``, only that section of the active environment is
    replaced. When the section is stored in a split file under ``settings/``,
    the values are merged into that file instead, under the section key the
    file wraps its content in. The values may include that key
    (``{"git": {...}}``) or leave it out. Without ``section``, the
    whole file is replaced, wrapped in a ``default:`` environment.

    Writing a section keeps the file's comments and the rest of its layout, except in
    the section replaced. Writing the whole file replaces it, comments included.

    Args:
        values: The settings to write. Without ``section``, a mapping without a ``default``
            key is wrapped in one.
        settings_file: The settings file. Default: the settings.yml or config.yml found from
            the current directory, else ``settings.yml`` in the current directory.
        section: The top-level section to replace, such as "directories", in the active
            environment (``active_environment()``), or at the top level of a flat file with
            no environments. Default: replace the whole file.

    Raises:
        SettingsError: When ``section`` is given and the settings file or its split file does
            not exist.
    """
    if settings_file is None:
        found = find_settings_file()
        path = found if found is not None else Path("settings.yml")
    else:
        path = Path(settings_file).expanduser()

    if section is None:
        data = dict(values)
        if "default" not in data:
            data = {"default": data}
        _yaml.write(path, data)
        return

    if not path.is_file():
        raise SettingsError(
            f"Settings file '{path}' does not exist. "
            "Call settings_write(values) without 'section' to create it first."
        )
    current = _yaml.read(path) or {}
    if _has_environment_sections(current):
        environment: str | None = active_environment()
        env_config = current.get(environment)
    else:
        # A flat file has no environments: its sections are at the top level.
        environment = None
        env_config = current
    reference = env_config.get(section) if isinstance(env_config, dict) else None

    if isinstance(reference, str) and reference.startswith("settings/"):
        split_path = path.parent / reference
        if not split_path.is_file():
            raise SettingsError(f"Split file '{reference}' does not exist")

        def merge_split(split: Any) -> Any:
            split = split if isinstance(split, dict) else {}
            if split and section not in split:
                # An older split file without the section key: wrap it, as R does.
                split = {section: split}
            return _modify_in_place(split, _split_values(section, values))

        _yaml.update(split_path, merge_split)
        return

    def replace_section(raw: Any) -> Any:
        raw = raw if isinstance(raw, dict) else {}
        target = raw
        if environment is not None:
            if not isinstance(raw.get(environment), dict):
                raw[environment] = {}
            target = raw[environment]
        target[section] = dict(values)
        return raw

    _yaml.update(path, replace_section)
