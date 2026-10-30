"""Regenerate the expected project trees in tests/golden.

Each case folder holds a layers file. This script creates the project
headless (no git, no registry) and stores the files under ``expected/``, with
the creation date and the package version replaced by fixed placeholders.
Review the diff before committing: these files define what Python creates.

    uv run python scripts/make_golden_trees.py
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"
DATE = "2000-01-01"
VERSION = "0.0.0-golden"
SKIP = {"framework.db"}


def normalize(text: str) -> str:
    text = re.sub(r"\d{4}-\d\d-\d\d", DATE, text)
    return re.sub(r'"framework_version": "[^"]*"', f'"framework_version": "{VERSION}"', text)


def build(case: Path) -> dict[str, str]:
    """Create the case's project and return its files as normalized text."""
    sys.path.insert(0, str(ROOT / "src"))
    from framework.create import project_create_from_layers

    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "project"
        project_create_from_layers(case / "layers.json", target)
        return {
            path.relative_to(target).as_posix(): normalize(path.read_text(encoding="utf-8"))
            for path in sorted(target.rglob("*"))
            if path.is_file() and path.name not in SKIP
        }


def main() -> int:
    for case in sorted(p for p in GOLDEN.iterdir() if (p / "layers.json").is_file()):
        expected = case / "expected"
        shutil.rmtree(expected, ignore_errors=True)
        for relative, text in build(case).items():
            target = expected / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        print(f"{case.name}: {sum(1 for p in expected.rglob('*') if p.is_file())} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
