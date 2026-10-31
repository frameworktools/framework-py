"""Build the docs site archive for py.framework.pub.

Writes ``reference.json`` from the docstrings of everything in
``framework.__all__``, checks it against ``docs-reference.v1.json``, and
packs ``docs/site`` with it into ``docs-site.tar.gz``. The format is R's
``inst/schema/docs-site.md``.

    python scripts/build_docs.py                        # check only
    python scripts/build_docs.py --reference out.json   # write reference.json
    python scripts/build_docs.py --archive dist-docs/docs-site.tar.gz

Docstrings are Google style. The first line is the summary, the paragraphs
before the first section are the description, and ``Args:`` (or
``Attributes:`` for classes), ``Returns:``, and ``Raises:`` become
parameters, returns, and a section.
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import io
import json
import re
import sys
import tarfile
from pathlib import Path
from typing import Any

import jsonschema
import yaml

import framework

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs" / "site"
SCHEMA = ROOT / "src" / "framework" / "_assets" / "schema" / "docs-reference.v1.json"

_SECTION = re.compile(r"^(Args|Arguments|Attributes|Returns|Yields|Raises|Examples?|Notes?):\s*$")
_ITEM = re.compile(r"^(\*{0,2}[A-Za-z_][A-Za-z0-9_]*)(?:\s*\(([^)]*)\))?:\s*(.*)$")


class DocsError(Exception):
    pass


# Docstrings -------------------------------------------------------------------


def _markdown(text: str) -> str:
    """reStructuredText inline code to Markdown, linking exported names."""
    exported = set(framework.__all__)

    def code(match: re.Match[str]) -> str:
        inner = match.group(1)
        name = inner.removesuffix("()")
        if name in exported:
            return f"[`{inner}`](ref:{name})"
        return f"`{inner}`"

    return re.sub(r"``(.+?)``", code, text).strip()


def _dedent_block(lines: list[str]) -> list[str]:
    indents = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(indents, default=0)
    return [line[cut:] for line in lines]


def parse_docstring(doc: str) -> dict[str, Any]:
    """Split a Google-style docstring into summary, description, and sections."""
    lines = doc.strip().splitlines()
    summary = lines[0].strip() if lines else ""
    body: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] = body
    for line in lines[1:]:
        match = _SECTION.match(line)
        if match:
            key = {"Arguments": "Args", "Example": "Examples", "Notes": "Note"}.get(
                match.group(1), match.group(1)
            )
            current = sections.setdefault(key, [])
            continue
        current.append(line)
    paragraphs = re.split(r"\n\s*\n", "\n".join(body).strip())
    paragraphs = [p for p in paragraphs if p.strip()]
    return {
        "summary": summary,
        "description": "\n\n".join(paragraphs[:2]),
        "details": "\n\n".join(paragraphs[2:]),
        "sections": {key: _dedent_block(value) for key, value in sections.items()},
    }


def parse_items(lines: list[str]) -> list[tuple[str, str]]:
    """``name: text`` items, with indented continuation lines folded in."""
    items: list[tuple[str, list[str]]] = []
    for line in lines:
        if not line.strip():
            if items:
                items[-1][1].append("")
            continue
        match = _ITEM.match(line) if not line.startswith(" ") else None
        if match:
            items.append((match.group(1), [match.group(3)]))
        elif items:
            items[-1][1].append(line.strip())
    return [
        (name, re.sub(r"(?<!\n)\n(?!\n)", " ", "\n".join(text).strip())) for name, text in items
    ]


# Entries ----------------------------------------------------------------------


def _annotation(value: Any) -> str | None:
    if value is inspect.Parameter.empty:
        return None
    return value if isinstance(value, str) else str(inspect.formatannotation(value))


def _prefix(param: inspect.Parameter) -> str:
    if param.kind is inspect.Parameter.VAR_POSITIONAL:
        return "*"
    if param.kind is inspect.Parameter.VAR_KEYWORD:
        return "**"
    return ""


def _parameter(param: inspect.Parameter) -> str:
    """A parameter as written in the source, shortening long defaults."""
    text = _prefix(param) + param.name
    annotation = _annotation(param.annotation)
    if annotation:
        text += f": {annotation}"
    if param.default is not param.empty:
        default = repr(param.default)
        if len(default) > 30:
            default = f"{default[0]}...{default[-1]}" if default[0] in "([{" else "..."
        text += f" = {default}" if annotation else f"={default}"
    return text


def _usage(name: str, signature: inspect.Signature) -> str:
    params: list[str] = []
    for param in signature.parameters.values():
        if param.name == "self":
            continue
        if param.kind == param.KEYWORD_ONLY and not any(p.startswith("*") for p in params):
            params.append("*")
        params.append(_parameter(param))
    returns = _annotation(signature.return_annotation)
    tail = f" -> {returns}" if returns else ""
    one_line = f"{name}({', '.join(params)}){tail}"
    if len(one_line) <= 88:
        return one_line
    inner = "".join(f"    {p},\n" for p in params)
    return f"{name}(\n{inner}){tail}"


def _signature(obj: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(obj)
    except (TypeError, ValueError):
        return None


def build_entry(name: str, obj: Any, category: str, problems: list[str]) -> dict[str, Any]:
    doc = inspect.getdoc(obj)
    if not doc:
        raise DocsError(f"{name} has no docstring")
    parsed = parse_docstring(doc)
    sections = parsed["sections"]
    is_class = inspect.isclass(obj)
    entry: dict[str, Any] = {
        "id": name,
        "name": name,
        "kind": "class" if is_class else "function",
        "category": category,
        "summary": _markdown(parsed["summary"]),
    }
    for key in ("description", "details"):
        if parsed[key]:
            entry[key] = _markdown(parsed[key])

    documented = dict(parse_items(sections.get("Attributes" if is_class else "Args", [])))
    parameters: list[dict[str, Any]] = []
    if is_class and dataclasses.is_dataclass(obj):
        for field in dataclasses.fields(obj):
            if field.name.startswith("_"):
                continue
            if field.name not in documented:
                problems.append(f"{name}: attribute '{field.name}' is not documented")
            item: dict[str, Any] = {
                "name": field.name,
                "description": _markdown(documented.get(field.name, "")),
            }
            if _annotation(field.type):
                item["type"] = _annotation(field.type)
            parameters.append(item)
    elif not is_class:
        signature = _signature(obj)
        if signature is None:
            raise DocsError(f"{name}: no signature")
        entry["usage"] = _usage(name, signature)
        for param in signature.parameters.values():
            key = _prefix(param)
            label = f"{key}{param.name}"
            text = documented.get(label) or documented.get(param.name)
            if text is None:
                problems.append(f"{name}: parameter '{param.name}' is not documented")
            item = {"name": label, "description": _markdown(text or "")}
            if _annotation(param.annotation):
                item["type"] = _annotation(param.annotation)
            if param.default is not param.empty:
                item["default"] = repr(param.default)
            elif key == "":
                item["required"] = True
            parameters.append(item)
    if parameters:
        entry["parameters"] = parameters

    for key in ("Returns", "Yields"):
        if key in sections:
            entry["returns"] = _markdown("\n".join(sections[key]))
    if "Note" in sections:
        entry["note"] = _markdown("\n".join(sections["Note"]))
    if "Raises" in sections:
        raises = "\n".join(
            f"- `{error}`: {_markdown(text)}" for error, text in parse_items(sections["Raises"])
        )
        entry["sections"] = [{"title": "Raises", "content": raises}]
    if "Examples" in sections:
        code = "\n".join(sections["Examples"]).strip()
        code = "\n".join(re.sub(r"^(>>> |\.\.\. )", "", line) for line in code.splitlines())
        entry["examples"] = [{"code": code}]
    return entry


def build_reference(categories: list[dict[str, Any]], problems: list[str]) -> dict[str, Any]:
    exported = list(framework.__all__)
    placed: dict[str, str] = {}
    for category in categories:
        for name in category["names"]:
            if name in placed:
                raise DocsError(f"{name} is in both {placed[name]} and {category['id']}")
            placed[name] = category["id"]
        for name in category.get("common", []):
            if name not in category["names"]:
                raise DocsError(f"{category['id']}: common name {name} is not in its names")
    documented = [name for name in exported if not name.startswith("__")]
    missing = sorted(set(documented) - set(placed))
    unknown = sorted(set(placed) - set(documented))
    if missing or unknown:
        raise DocsError(
            f"categories.yml: missing {missing or 'none'}; not exported {unknown or 'none'}"
        )
    entries = [
        build_entry(name, getattr(framework, name), placed[name], problems)
        for category in categories
        for name in category["names"]
    ]
    return {
        "schema": "docs-reference.v1",
        "product": "py",
        "version": framework.__version__,
        "categories": [
            {
                key: value
                for key, value in {
                    "id": category["id"],
                    "title": category["title"],
                    "description": category.get("description"),
                    "position": position,
                    "common": category.get("common"),
                }.items()
                if value is not None
            }
            for position, category in enumerate(categories, start=1)
        ],
        "entries": entries,
    }


# Site -------------------------------------------------------------------------


def _prose(markdown: str) -> str:
    """Markdown without fenced code blocks, whose lines are not headings or links."""
    return re.sub(r"^(```|~~~).*?^\1[ \t]*$", "", markdown, flags=re.M | re.S)


def check_site(reference: dict[str, Any]) -> list[str]:
    """Check site.yml, the guides' front matter, and every guide:/ref: link."""
    problems: list[str] = []
    site = yaml.safe_load((SITE / "site.yml").read_text(encoding="utf-8"))
    sections = {section["id"] for section in site["sections"]}
    guides: dict[str, Path] = {}
    for path in sorted((SITE / "guides").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
        if not match:
            problems.append(f"{path.name}: no front matter")
            continue
        meta = yaml.safe_load(match.group(1)) or {}
        for key in ("id", "title", "description", "section", "position"):
            if key not in meta:
                problems.append(f"{path.name}: front matter is missing {key}")
        if meta.get("section") not in sections:
            problems.append(f"{path.name}: unknown section {meta.get('section')}")
        if meta.get("id") != path.stem:
            problems.append(f"{path.name}: id {meta.get('id')} does not match the file name")
        if re.search(r"^# ", _prose(text[match.end() :]), re.M):
            problems.append(f"{path.name}: guides have no h1")
        guides[path.stem] = path
    ids = {entry["id"] for entry in reference["entries"]}
    texts = [(path.name, _prose(path.read_text(encoding="utf-8"))) for path in guides.values()]
    texts.append(("reference.json", json.dumps(reference)))
    for source, text in texts:
        for kind, target in re.findall(r"\((guide|ref):([^)#\s]+)", text):
            if kind == "guide" and target not in guides:
                problems.append(f"{source}: no guide {target}")
            if kind == "ref" and target not in ids:
                problems.append(f"{source}: no reference entry {target}")
        for image in re.findall(r"\]\((images/[^)\s]+)\)", text):
            if not (SITE / image).is_file():
                problems.append(f"{source}: missing {image}")
    return problems


def write_archive(reference: dict[str, Any], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target, "w:gz") as archive:
        for path in sorted(SITE.rglob("*")):
            if path.is_file() and path.name != "categories.yml":
                archive.add(path, arcname=f"docs-site/{path.relative_to(SITE).as_posix()}")
        data = (json.dumps(reference, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        info = tarfile.TarInfo("docs-site/reference.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--reference", type=Path, help="write reference.json here")
    parser.add_argument("--archive", type=Path, help="write docs-site.tar.gz here")
    parser.add_argument("--schema", type=Path, default=SCHEMA, help="docs-reference.v1.json")
    args = parser.parse_args(argv)
    problems: list[str] = []
    try:
        categories = yaml.safe_load((SITE / "categories.yml").read_text(encoding="utf-8"))
        reference = build_reference(categories, problems)
    except DocsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(reference), key=lambda e: e.path
    )
    problems += [f"reference.json: {list(e.path)}: {e.message}" for e in errors]
    problems += check_site(reference)
    if problems:
        print("\n".join(f"error: {problem}" for problem in problems), file=sys.stderr)
        return 1
    if args.reference:
        args.reference.write_text(json.dumps(reference, indent=2, ensure_ascii=False) + "\n")
    if args.archive:
        write_archive(reference, args.archive)
    guides = len(list((SITE / "guides").glob("*.md")))
    print(f"{len(reference['entries'])} reference entries and {guides} guides are valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
