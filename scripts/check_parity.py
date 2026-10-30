"""Check parity.toml against the R package's exports and framework.__all__.

Fails when:

- an R export has no entry, or an entry names a function R no longer exports,
- an entry has an unknown status, or a ported, renamed, or folded entry has no
  ``python`` name,
- framework.__all__ has a function that is neither in parity.toml nor listed
  as Python-only.

Entries whose Python function does not exist yet are reported as pending.
With ``--strict`` (used for releases), pending entries fail too.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
NAMESPACE = ROOT / "tests" / "shared" / "NAMESPACE"
MANIFEST = ROOT / "parity.toml"

STATUSES = {"ported", "renamed", "folded", "r-only", "deprecated", "omitted"}
NEEDS_PYTHON = {"ported", "renamed", "folded"}


def r_exports(path: Path = NAMESPACE) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"^export\(([^)]+)\)$", text, re.MULTILINE))


def python_exports() -> set[str]:
    sys.path.insert(0, str(ROOT / "src"))
    import framework

    # Classes (errors, result types) and dunders are not functions to map.
    return {name for name in framework.__all__ if name[0].islower() and not name.startswith("__")}


def check(strict: bool) -> int:
    manifest: dict[str, Any] = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    entries: dict[str, dict[str, str]] = manifest.get("functions", {})
    python_only = set(manifest.get("python_only", {}).get("names", []))
    exports = r_exports()
    available = python_exports()

    problems: list[str] = []
    for name in sorted(exports - set(entries)):
        problems.append(f"R exports {name}() but parity.toml has no entry for it")
    for name in sorted(set(entries) - exports):
        problems.append(f"parity.toml has {name}, which R no longer exports")

    pending: list[str] = []
    mapped: set[str] = set()
    for name, entry in sorted(entries.items()):
        status = entry.get("status")
        if status not in STATUSES:
            problems.append(f"{name}: unknown status {status!r}")
            continue
        if status not in NEEDS_PYTHON:
            continue
        target = entry.get("python")
        if not target:
            problems.append(f"{name}: status {status} needs a python name")
            continue
        mapped.add(target)
        if target not in available:
            pending.append(f"{name} -> {target}")

    for name in sorted(available - mapped - python_only):
        problems.append(
            f"framework.{name} is not in parity.toml (map it, or list it as python_only)"
        )

    for problem in problems:
        print(f"error: {problem}")

    done = len(exports) - len(pending)
    print(f"{done} of {len(exports)} R exports are handled; {len(pending)} pending in Python.")
    if strict and pending:
        for item in pending:
            print(f"pending: {item}")
    return 1 if problems or (strict and pending) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check parity.toml")
    parser.add_argument("--strict", action="store_true", help="fail on pending entries")
    return check(parser.parse_args().strict)


if __name__ == "__main__":
    sys.exit(main())
