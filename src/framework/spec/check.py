"""Checking a resolved project spec before anything is written."""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

from framework import _yaml
from framework.spec.merge import MAP_PATHS

REQUIRED = (
    "spec_version",
    "name",
    "type",
    "language",
    "author",
    "directories",
    "extra_directories",
    "render_dirs",
    "packages",
    "git",
    "ai",
    "scaffold",
    "connections",
    "security",
    "env",
    "data_hooks",
    "files",
    "skills",
)
OPTIONAL = ("quarto", "source", "tools", "core_files", "settings_layout", "publish", "data")

# The data registry: the fields a catalog entry carries in a spec and to and from
# Framework Cloud, the types data_read() reads (with R's aliases), and its names.
DATA_FIELDS = ("path", "type", "delimiter", "locked", "private", "description", "stata_names")
DATA_TYPES = (
    "csv", "tsv", "rds", "excel", "xlsx", "xls", "parquet", "stata", "dta",
    "spss", "sav", "zsav", "spss_por", "por", "sas", "sas7bdat", "sas7bcat", "sas_xpt", "xpt",
)  # fmt: skip
DATA_DELIMITERS = ("comma", "tab", "semicolon", "space")
_DATA_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*([.][A-Za-z0-9_][A-Za-z0-9_-]*)*$")

# Assistant -> (skills folder, stub file). Adding an assistant is one row.
ASSISTANTS: dict[str, tuple[str, str | None]] = {
    "claude": (".claude/skills", "CLAUDE.md"),
    "codex": (".agents/skills", None),
    "copilot": (".agents/skills", ".github/copilot-instructions.md"),
}

_LABEL = re.compile(r"^[a-z][a-z0-9_]*$")
_SOURCES = ("pypi", "github")
_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SKILL = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_NAME = re.compile(
    r"PASSWORD|SECRET|TOKEN|ACCESS_KEY|API_KEY|PRIVATE_KEY|CREDENTIAL", re.IGNORECASE
)
_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def secret_env_values(raw: str | None = None, variables: Any = None) -> list[str]:
    """Secret-looking variables that have a value, as R's ``.fw_secret_env_values()``.

    ``variables`` maps names to values. ``raw`` is .env text; ``export``,
    quotes, and inline comments are handled, so ``KEY= # note`` is empty.
    """
    found: list[str] = []
    for name, value in (variables if isinstance(variables, dict) else {}).items():
        if _SECRET_NAME.search(str(name)) and value not in (None, ""):
            found.append(str(name))
    for line in (raw or "").split("\n"):
        line = re.sub(r"^export\s+", "", line.strip())
        match = _ENV_LINE.match(line)
        if match is None:
            continue
        value = re.sub(r"(^|\s+)#.*$", "", match.group(2).strip())
        value = re.sub(r"^[\"']|[\"']$", "", value)
        if _SECRET_NAME.search(match.group(1)) and value:
            found.append(match.group(1))
    return list(dict.fromkeys(found))


_HOOK = re.compile(r"^[^:]+\.py::[A-Za-z_][A-Za-z0-9_.]*$")
# Workflow -> the notebook format it allows. R Markdown is R only.
WORKFLOW_FORMATS = {"quarto": "quarto", "jupyter": "jupyter", "scripts": "script"}
_NOTEBOOK_FORMATS = tuple(WORKFLOW_FORMATS.values())


def is_safe_path(path: Any) -> bool:
    """A relative path that stays inside the project."""
    return (
        isinstance(path, str)
        and bool(path)
        and not re.match(r"^(/|[A-Za-z]:|~)", path)
        and "\\" not in path
        and ".." not in path.split("/")
    )


def skill_name(content: str) -> str | None:
    """The ``name`` in a SKILL.md file's frontmatter."""
    lines = re.split(r"\r?\n", content)
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if end is None:
        return None
    try:
        front = _yaml.load("\n".join(lines[1:end]))
    except Exception:
        return None
    name = front.get("name") if isinstance(front, dict) else None
    return name if isinstance(name, str) else None


def shipped_skill(name: str) -> bool:
    """Whether this package ships a skill with this name."""
    return resources.files("framework").joinpath(f"templates/skills/{name}/SKILL.md").is_file()


def _get(spec: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = spec
    for part in path:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def spec_check(spec: dict[str, Any]) -> list[str]:
    """Problems that would stop ``project_create()``. An empty list means the spec is valid.

    Args:
        spec: A resolved spec, such as ``spec_resolve().spec``. It is checked for required and
            unknown sections, the spec version, project type, and language, safe relative
            paths, package and skill entries, and AI and scaffold settings.

    Returns:
        One message per problem found.
    """
    problems: list[str] = []
    add = problems.append

    for key in REQUIRED:
        if key not in spec:
            add(f"missing section '{key}'")
    for key in spec:
        if key not in REQUIRED and key not in OPTIONAL:
            add(f"unknown section '{key}'")

    if spec.get("spec_version") != 1:
        add(f"spec_version must be 1, got {spec.get('spec_version')}")
    # type is a label; behavior follows settings, tools, and core_files
    if not isinstance(spec.get("type"), str) or not _LABEL.match(spec["type"]):
        add(f"type must be a lowercase id such as 'project', got '{spec.get('type')}'")
    if spec.get("core_files") is not None and not isinstance(spec["core_files"], bool):
        add("core_files must be true or false")
    if spec.get("settings_layout") not in (None, "split", "flat"):
        add("settings_layout must be 'split' or 'flat'")
    for tool, on in _dict(spec.get("tools")).items():
        if not _LABEL.match(str(tool)):
            add(f"tools: invalid tool id '{tool}'")
        if not isinstance(on, bool):
            add(f"tools.{tool} must be true or false")
    if spec.get("language") != "python":
        add(f"language must be 'python' for the Python package, got '{spec.get('language')}'")

    if spec.get("publish") is not None:
        publish = spec["publish"]
        if not isinstance(publish, dict):
            add("publish must be an object")
        else:
            from framework.errors import SettingsError
            from framework.settings import _check_publish_credentials

            try:
                _check_publish_credentials(publish)
            except SettingsError as error:
                add(str(error))
            destinations = publish.get("destinations", {})
            if not isinstance(destinations, dict):
                add("publish.destinations must be an object keyed by name")
            else:
                default = publish.get("default")
                if default is not None and (
                    not isinstance(default, str) or default not in destinations
                ):
                    add("publish.default must name a configured destination")
                for name, entry in destinations.items():
                    if not isinstance(entry, dict) or entry.get("driver") not in (
                        "cloud",
                        "github",
                        "folder",
                        "s3",
                    ):
                        add(f"publish.destinations.{name}: unknown publishing driver")

    for map_path in MAP_PATHS:
        value = _get(spec, map_path)
        if value not in (None, {}) and not isinstance(value, dict):
            add(f"{'.'.join(map_path)} must be an object keyed by name, not a list")

    def check_path(path: Any, where: str) -> None:
        if not is_safe_path(path):
            add(f"{where}: path '{path}' must be relative and stay inside the project")

    for key, path in _dict(spec.get("directories")).items():
        check_path(path, f"directories.{key}")
    for key, path in _dict(spec.get("render_dirs")).items():
        check_path(path, f"render_dirs.{key}")
    for key, entry in _dict(spec.get("extra_directories")).items():
        path = _dict(entry).get("path")
        if path is None:
            add(f"extra_directories.{key}: missing path")
        else:
            check_path(path, f"extra_directories.{key}")
    for path, entry in _dict(spec.get("files")).items():
        check_path(path, "files")
        if not isinstance(_dict(entry).get("content"), str):
            add(f"files.{path}: missing content")

    for name, entry in _dict(_get(spec, ("packages", "default_packages"))).items():
        if not _PACKAGE.match(name):
            add(f"packages.default_packages: invalid package name '{name}'")
        if _dict(entry).get("source") not in _SOURCES:
            add(
                f"packages.default_packages.{name}: "
                "source must be pypi or github for a Python project"
            )

    for assistant in _dict(_get(spec, ("ai", "assistants"))):
        if assistant not in ASSISTANTS:
            add(f"ai.assistants: unknown assistant '{assistant}'")
    if _get(spec, ("ai", "agents_md")) not in ("stub", "none"):
        add("ai.agents_md must be 'stub' or 'none'")

    notebook_format = _get(spec, ("scaffold", "notebook_format"))
    if notebook_format is not None and notebook_format not in _NOTEBOOK_FORMATS:
        add(
            "scaffold.notebook_format must be quarto, jupyter, or script for a Python "
            f"project, not '{notebook_format}'"
        )
    workflow = _get(spec, ("scaffold", "workflow"))
    if workflow is not None and workflow not in WORKFLOW_FORMATS:
        add(f"scaffold.workflow must be quarto, jupyter, or scripts, not '{workflow}'")
    elif (
        workflow is not None
        and notebook_format is not None
        and notebook_format != WORKFLOW_FORMATS[workflow]
    ):
        add(
            f"scaffold.notebook_format '{notebook_format}' doesn't fit the {workflow} "
            f"workflow, which uses '{WORKFLOW_FORMATS[workflow]}'"
        )

    for name, entry in _dict(spec.get("skills")).items():
        entry = _dict(entry)
        if not _SKILL.match(name):
            add(f"skills: invalid skill name '{name}'")
        if entry.get("package") is True:
            if not shipped_skill(name):
                add(f"skills.{name}: not shipped with this version of framework-py")
        elif isinstance(entry.get("content"), str):
            declared = skill_name(entry["content"])
            if declared != name and not entry.get("sync"):
                add(f"skills.{name}: SKILL.md frontmatter name is '{declared or 'missing'}'")
            for path in _dict(entry.get("files")):
                check_path(path, f"skills.{name}.files")
        else:
            add(f"skills.{name}: needs either package: true or content")

    for name, value in _dict(spec.get("env")).items():
        if not _ENV_NAME.match(name):
            add(f"env: invalid variable name '{name}'")
        elif _SECRET_NAME.search(name) and value not in (None, ""):
            add(f"env.{name}: secrets must not be stored in the spec; leave the value empty")
    problems.extend(data_registry_problems(spec.get("data")))
    for key, hook in _dict(spec.get("data_hooks")).items():
        if not isinstance(hook, str) or not _HOOK.match(hook):
            add(f"data_hooks.{key}: expected 'path/to/file.py::function'")

    return problems


def data_registry_problems(data: Any) -> list[str]:
    """Problems with a spec's ``data`` section (catalog name -> entry), as R's
    spec_check() and the schema's data_entry see them."""
    if data is None or data == {} or data == []:
        return []
    if not isinstance(data, dict):
        return ["data must be an object keyed by catalog name, not a list"]
    problems: list[str] = []
    add = problems.append
    for name, entry in data.items():
        where = f"data.{name}"
        if not _DATA_NAME.match(str(name)):
            add(f"data: invalid catalog name '{name}'")
        if not isinstance(entry, dict):
            add(f"{where}: must be an entry with a path")
            continue
        for field in entry:
            if field not in DATA_FIELDS:
                add(f"{where}: unknown field '{field}'")
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            add(f"{where}: missing path")
        elif not is_safe_path(path):
            add(f"{where}: path '{path}' must be relative and stay inside the project")
        if entry.get("type") is not None and entry["type"] not in DATA_TYPES:
            add(f"{where}: type must be one of {', '.join(DATA_TYPES)}")
        if entry.get("delimiter") is not None and entry["delimiter"] not in DATA_DELIMITERS:
            add(f"{where}: delimiter must be comma, tab, semicolon, or space")
        for flag in ("locked", "private"):
            if entry.get(flag) is not None and not isinstance(entry[flag], bool):
                add(f"{where}.{flag} must be true or false")
        if entry.get("description") is not None and not isinstance(entry["description"], str):
            add(f"{where}.description must be text")
        names = entry.get("stata_names")
        if names and (
            not isinstance(names, dict) or not all(isinstance(v, str) for v in names.values())
        ):
            add(f"{where}.stata_names must map column names to Stata names")
    # A name that is also a group of other names can't be written nested
    for name in data:
        inside = [other for other in data if str(other).startswith(f"{name}.")]
        if inside:
            add(f"data: '{name}' is an entry, so '{inside[0]}' can't be inside it")
    return problems


def catalog_nest(data: Any) -> dict[str, Any]:
    """A spec's ``data`` section (dot-notation names) as the nested catalog the settings
    hold: ``inputs.raw.survey`` becomes ``inputs: raw: survey:``."""
    catalog: dict[str, Any] = {}
    for name, entry in (data if isinstance(data, dict) else {}).items():
        if entry is None:
            continue
        node = catalog
        *groups, last = str(name).split(".")
        for part in groups:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[last] = entry
    return catalog
