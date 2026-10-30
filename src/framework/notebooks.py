"""New notebooks and scripts from stubs: ``make_notebook()`` and friends.

A stub is a template file named ``notebook-<stub>.<ext>`` or
``script-<stub>.py``. Framework looks in the project's ``stubs/`` folder
first, then in the stubs it ships. ``stubs_publish()`` copies the shipped
stubs into ``stubs/`` so you can edit them.

A template saved with ``write_framework_template()`` comes between the two.

Stubs can use these placeholders: ``{{ title }}``, ``{{ filename }}``,
``{{ date }}``, ``{{ author }}``, ``{{ email }}``, and ``{{ affiliation }}``.
In a Quarto stub, an ``author: "{{ author }}"`` line becomes a full author
block when the author has an ORCID, URL, or affiliations, as in R.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import date
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from framework._log import inform, show_messages
from framework.config_home import global_settings
from framework.errors import FrameworkError
from framework.global_config import templates_dir
from framework.project import find_settings_file
from framework.settings import settings_read
from framework.spec.resolve import author_merge, author_normalize

NotebookType = Literal["quarto", "jupyter", "script"]
_EXTENSIONS: dict[NotebookType, str] = {"quarto": "qmd", "jupyter": "ipynb", "script": "py"}
_TYPES: dict[str, NotebookType] = {ext: kind for kind, ext in _EXTENSIONS.items()}
_STUB_FILE = re.compile(r"^(notebook|script)-(.+)\.(qmd|ipynb|py)$")
_PLACEHOLDERS = {
    "name": "Your Name",
    "email": "your.email@example.com",
    "affiliation": "Your Institution",
}
_AFFILIATION_FIELDS = ("name", "department", "address", "city", "country", "url")


def _shipped_stubs() -> Path:
    with as_file(files("framework").joinpath("templates/stubs")) as path:
        return Path(path)


def _project() -> tuple[Path, dict[str, Any]]:
    """The project root and settings, or the current folder and no settings."""
    found = find_settings_file()
    if found is None:
        return Path.cwd(), {}
    try:
        return found.parent, settings_read(found)
    except FrameworkError:
        return found.parent, {}


def slugify(text: str) -> str:
    """Lowercase letters, digits, and hyphens, as R names notebook files."""
    slug = re.sub(r"[^a-z0-9-]", "-", text.lower())
    return re.sub(r"-+", "-", slug).strip("-")


def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    return value if isinstance(value, dict) else {}


# Authors ----------------------------------------------------------------------


def _author(config: dict[str, Any]) -> dict[str, Any]:
    """The project author over the global author, without the default placeholders."""
    global_author = dict(_section(global_settings(), "author"))
    for key, placeholder in _PLACEHOLDERS.items():
        if global_author.get(key) == placeholder:
            global_author[key] = ""
    return author_merge(global_author, _section(config, "author"))


def _yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def author_front_matter(author: dict[str, Any], default_name: str = "Your Name") -> list[str]:
    """Quarto front matter lines for an author, matching R's ``.author_front_matter()``."""
    author = author_normalize(author)
    name = author["name"] or default_name
    if not any(key in author for key in ("orcid", "url", "affiliations")):
        return [f"author: {_yaml_quote(name)}"]
    lines = ["author:", f"  - name: {_yaml_quote(name)}"]
    for key in ("email", "orcid", "url"):
        if author.get(key):
            lines.append(f"    {key}: {_yaml_quote(author[key])}")
    affiliations = author.get("affiliations") or (
        [{"name": author["affiliation"]}] if author.get("affiliation") else []
    )
    if affiliations:
        lines.append("    affiliations:")
        for affiliation in affiliations:
            lines.append(f"      - name: {_yaml_quote(affiliation['name'])}")
            for key in _AFFILIATION_FIELDS[1:]:
                if affiliation.get(key):
                    lines.append(f"        {key}: {_yaml_quote(affiliation[key])}")
    return lines


def _fill(content: str, ext: str, values: dict[str, str], author: dict[str, Any]) -> str:
    if ext == "qmd":
        content = re.sub(
            r'(?m)^author:\s*"\{\{\s*author\s*\}\}"\s*$',
            lambda _: "\n".join(author_front_matter(author)),
            content,
            count=1,
        )
    for name, value in values.items():
        # Notebook files are JSON, so values go in as JSON string contents.
        text = json.dumps(value)[1:-1] if ext == "ipynb" else value
        # Escape backslashes: re.sub would read them as group references.
        content = re.sub(r"\{\{\s*" + name + r"\s*\}\}", text.replace("\\", "\\\\"), content)
        content = content.replace("{" + name + "}", text)
    return content


# Stubs ------------------------------------------------------------------------


def _find_stub(root: Path, prefix: str, stub: str, ext: str) -> Path:
    filename = f"{prefix}-{stub}.{ext}"
    user = root / "stubs" / filename
    if user.is_file():
        inform(f"Using user stub: {user}")
        return user
    personal = templates_dir() / filename
    if personal.is_file():
        return personal
    shipped = _shipped_stubs() / filename
    if shipped.is_file():
        return shipped
    available = sorted(stubs_list(_TYPES[ext])["name"])
    raise FrameworkError(
        f"Stub template not found: {stub}\n\nSearched:\n  - {user}\n  - Framework's {filename}\n\n"
        f"Available stubs: {', '.join(available) or 'none'}"
    )


def _normalize(name: str, kind: NotebookType) -> tuple[str, NotebookType, str]:
    """The file name, notebook type, and extension; an extension in ``name`` wins."""
    match = re.search(r"\.(qmd|ipynb|py)$", name, re.IGNORECASE)
    if match:
        ext = match.group(1).lower()
        kind = _TYPES[ext]
        name = name[: match.start()]
    ext = _EXTENSIONS[kind]
    slug = slugify(name)
    if not slug:
        raise ValueError(f"Cannot make a file name from {name!r}")
    return f"{slug}.{ext}", kind, ext


def _default_dir(config: dict[str, Any], root: Path, keys: tuple[str, ...], option: str) -> Path:
    directories = _section(config, "directories")
    for key in keys:
        if isinstance(directories.get(key), str) and directories[key]:
            return root / str(directories[key])
    options = _section(config, "options")
    if isinstance(options.get(option), str) and options[option]:
        return root / str(options[option])
    for fallback in keys:
        if (root / fallback).is_dir():
            return root / fallback
    return root


def _default_type(config: dict[str, Any]) -> NotebookType:
    value = (
        _section(config, "scaffold").get("notebook_format")
        or _section(config, "options").get("default_notebook_format")
        or config.get("default_notebook_format")
    )
    if value in ("jupyter", "ipynb"):
        return "jupyter"
    return "quarto"


# Public API -------------------------------------------------------------------


def make_notebook(
    name: str,
    type: NotebookType | None = None,
    dir: str | Path | None = None,
    stub: str = "default",
    *,
    overwrite: bool = False,
    subdir: str | None = None,
) -> Path:
    """Create a notebook from a stub.

    Args:
        name: The notebook name. It is turned into a file name such as
            ``data-cleaning.qmd``. An extension (``.qmd``, ``.ipynb``, or
            ``.py``) sets the type.
        type: ``"quarto"`` (the default), ``"jupyter"``, or ``"script"``.
            Without it, ``scaffold: notebook_format`` in the settings decides.
        dir: The folder. Defaults to ``directories: notebooks`` in the
            settings, relative to the project root.
        stub: The stub to start from, such as ``"default"`` or ``"minimal"``.
        overwrite: Replace an existing file.
        subdir: A subfolder of ``dir``.

    Returns:
        The path of the new file.
    """
    show_messages()
    root, config = _project()
    if type is not None and type not in _EXTENSIONS:
        raise ValueError("type must be 'quarto', 'jupyter', or 'script'")
    filename, kind, ext = _normalize(name, type or _default_type(config))
    if dir is None:
        folder = (
            _default_dir(config, root, ("scripts",), "script_dir")
            if kind == "script"
            else _default_dir(config, root, ("notebooks", "work"), "notebook_dir")
        )
    else:
        folder = Path(dir)
    if subdir:
        folder = folder / subdir
    if not folder.exists():
        folder.mkdir(parents=True)
        inform(f"Created directory: {folder}")
    target = folder / filename
    if target.exists() and not overwrite:
        raise FrameworkError(f"File already exists: {target}\nUse overwrite=True to replace it.")

    source = _find_stub(root, "script" if kind == "script" else "notebook", stub, ext)
    author = _author(config)
    title = re.sub(r"\.(qmd|ipynb|py)$", "", Path(name).name, flags=re.IGNORECASE)
    values = {
        "title": title,
        "filename": title,
        "date": f"{date.today():%Y-%m-%d}",
        "author": author.get("name") or "Your Name",
        "email": author.get("email", ""),
        "affiliation": author.get("affiliation", ""),
    }
    content = _fill(source.read_text(encoding="utf-8"), ext, values, author)
    target.write_text(content, encoding="utf-8")
    label = {"quarto": "Quarto notebook", "jupyter": "Jupyter notebook", "script": "Python script"}
    inform(f"Created {label[kind]}: {target}")
    if stub != "default":
        inform(f"  Using stub: {stub}")
    return target


def make_qmd(
    name: str,
    dir: str | Path | None = None,
    stub: str = "default",
    *,
    overwrite: bool = False,
    subdir: str | None = None,
) -> Path:
    """Create a Quarto notebook (``.qmd``) with Python code chunks."""
    return make_notebook(name, "quarto", dir, stub, overwrite=overwrite, subdir=subdir)


def make_revealjs(
    name: str, dir: str | Path | None = None, *, overwrite: bool = False, subdir: str | None = None
) -> Path:
    """Create a Quarto reveal.js presentation."""
    return make_notebook(name, "quarto", dir, "revealjs", overwrite=overwrite, subdir=subdir)


def make_presentation(
    name: str, dir: str | Path | None = None, *, overwrite: bool = False, subdir: str | None = None
) -> Path:
    """Create a presentation. The same as ``make_revealjs()``."""
    return make_revealjs(name, dir, overwrite=overwrite, subdir=subdir)


def make_script(
    name: str, dir: str | Path | None = None, stub: str = "default", *, overwrite: bool = False
) -> Path:
    """Create a Python script (``.py``) in the scripts folder."""
    if not name.lower().endswith(".py"):
        name = f"{name}.py"
    return make_notebook(name, "script", dir, stub, overwrite=overwrite)


def stubs_list(type: NotebookType | None = None) -> pd.DataFrame:
    """The available stubs: the project's own, then Framework's.

    Returns:
        A DataFrame with columns name, type, and source (``user`` or
        ``framework``). A project stub hides Framework's of the same name.
    """
    if type is not None and type not in _EXTENSIONS:
        raise ValueError("type must be 'quarto', 'jupyter', or 'script'")
    root, _ = _project()
    rows = []
    for source, folder in (("user", root / "stubs"), ("framework", _shipped_stubs())):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            match = _STUB_FILE.match(path.name)
            if match:
                rows.append(
                    {"name": match.group(2), "type": _TYPES[match.group(3)], "source": source}
                )
    frame = pd.DataFrame(rows, columns=["name", "type", "source"])
    if type is not None:
        frame = frame[frame["type"] == type]
    frame = frame.drop_duplicates(["name", "type"]).sort_values(["name", "type"])
    return frame.reset_index(drop=True)


def stubs_path(which: Literal["auto", "user", "framework"] = "auto") -> Path:
    """Where stubs are read from: the project's ``stubs/`` folder or Framework's.

    ``"auto"`` gives the project folder when it exists.
    """
    if which not in ("auto", "user", "framework"):
        raise ValueError("which must be 'auto', 'user', or 'framework'")
    root, _ = _project()
    user = root / "stubs"
    if which == "user" or (which == "auto" and user.is_dir()):
        return user
    return _shipped_stubs()


def stubs_publish(
    type: str | list[str] = "all", *, overwrite: bool = False, stubs: list[str] | None = None
) -> list[Path]:
    """Copy Framework's stubs into the project's ``stubs/`` folder to customize them.

    Args:
        type: ``"all"``, ``"notebooks"``, or ``"scripts"``, or a list of them.
        overwrite: Replace stubs already in ``stubs/``.
        stubs: Only these stub names, such as ``["default"]``.

    Returns:
        The files copied.
    """
    show_messages()
    kinds = {type} if isinstance(type, str) else set(type)
    if not kinds <= {"all", "notebooks", "scripts"}:
        raise ValueError("type must be 'all', 'notebooks', or 'scripts'")
    if "all" in kinds:
        kinds = {"notebooks", "scripts"}
    root, _ = _project()
    target_dir = root / "stubs"
    if not target_dir.exists():
        target_dir.mkdir(parents=True)
        inform(f"Created directory: {target_dir}/")
    published: list[Path] = []
    for source in sorted(_shipped_stubs().iterdir()):
        match = _STUB_FILE.match(source.name)
        if match is None:
            continue
        if f"{match.group(1)}s" not in kinds:
            continue
        if stubs is not None and match.group(2) not in stubs:
            continue
        target = target_dir / source.name
        if target.exists() and not overwrite:
            inform(f"Skipped (exists): {target}")
            continue
        shutil.copyfile(source, target)
        inform(f"Published: {target}")
        published.append(target)
    if published:
        inform(f"Published {len(published)} stub(s) to {target_dir}/")
        inform("  Edit these files to customize your templates.")
    else:
        inform("No stubs were published.")
    return published
