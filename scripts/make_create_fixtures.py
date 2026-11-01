"""Generate the create/python/ conformance cases for the R repository.

R holds the one copy of the conformance fixtures, and every engine pins it.
Write the cases to a folder outside the R repository and hand them to its
maintainers, who copy them into ``inst/conformance/create/python/``.
Each case records the layers it was resolved from (for reference), the
resolved spec (the input to creation), and ``expected.paths``: every file and
folder that creating the spec writes, folders ending in ``/``, ``.git/`` left
out, sorted by byte value. The format is described in R's
``inst/conformance/README.md``.

    uv run python scripts/make_create_fixtures.py --to DIR
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
NAME = "Fixture Study"
AUTHOR = {"name": "Ada Analyst", "email": "ada@example.org", "affiliation": ""}

# id -> (structure, workflow, choices, description)
CASES: dict[str, tuple[str, str, dict[str, Any], str]] = {}


def _case(
    structure: str,
    workflow: str,
    description: str,
    *,
    suffix: str = "",
    ide: str = "vscode",
    agents: bool = True,
) -> None:
    choices: dict[str, Any] = {"git": {"use_git": False}, "scaffold": {"ide": ide}}
    if not agents:
        choices["ai"] = {"enabled": False}
    CASES[f"{structure}-{workflow}{suffix}"] = (structure, workflow, choices, description)


_case("project", "jupyter", "Standard structure, Jupyter workflow: Jupyter and Quarto on.")
_case("project", "quarto", "Standard structure, Quarto workflow, VS Code, agents on.")
_case("project", "scripts", "Standard structure, Scripts only: no Quarto or Jupyter files.")
_case("project", "jupyter", "Editor rstudio: no editor files.", suffix="-rstudio", ide="rstudio")
_case("project", "jupyter", "Editor both: a .code-workspace.", suffix="-both", ide="both")
_case("project", "jupyter", "Editor none: no editor files.", suffix="-none", ide="none")
_case(
    "project",
    "jupyter",
    "AI agents off: no AGENTS.md, stubs, or skills.",
    suffix="-no-agents",
    agents=False,
)
for _workflow in ("jupyter", "quarto", "scripts"):
    _case(
        "project_sensitive",
        _workflow,
        f"Privacy Sensitive structure, {_workflow.capitalize()} workflow, with agent guards.",
    )
    _case("bare", _workflow, f"Bare structure with the {_workflow.capitalize()} workflow.")
_case("presentation", "quarto", "Presentation structure, Quarto workflow.")
_case("course", "quarto", "Course structure, Quarto workflow.")


def created_paths(spec: dict[str, Any]) -> list[str]:
    from framework.create import project_create

    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "project"
        project_create(spec, target)
        paths = [
            f"{p.relative_to(target).as_posix()}/"
            if p.is_dir()
            else p.relative_to(target).as_posix()
            for p in target.rglob("*")
            if p.relative_to(target).parts[0] != ".git"
        ]
    return sorted(paths, key=lambda path: path.encode("utf-8"))


def build(
    structure: str, workflow: str, choices: dict[str, Any], description: str
) -> dict[str, Any]:
    from framework.features import workflow_layers
    from framework.spec.resolve import spec_resolve

    layers = [
        {"kind": "base", "key": structure, "layer": {"type": structure, "language": "python"}},
        *workflow_layers(workflow),
        {"kind": "project", "key": "choices", "layer": choices},
    ]
    spec = spec_resolve(layers, NAME, AUTHOR).spec
    return {
        "description": description,
        "language": "python",
        "layers": layers,
        "spec": spec,
        "expected": {"paths": created_paths(spec)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", type=Path, required=True, help="the folder to write the cases to")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    from framework._log import logger

    logger.disabled = True
    args.to.mkdir(parents=True, exist_ok=True)
    for old in args.to.glob("*.json"):
        old.unlink()
    for case_id, case in CASES.items():
        text = json.dumps(build(*case), indent=2, ensure_ascii=False) + "\n"
        (args.to / f"{case_id}.json").write_text(text, encoding="utf-8")
    print(f"Wrote {len(CASES)} cases to {args.to}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
