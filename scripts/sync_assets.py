"""Copy shared files from the R package and check that the copies are current.

The R repository holds the canonical schemas, framework.db schema, pre-commit
hook, conformance fixtures, and export list. This script copies them into framework-py and
records each file's SHA-256 in ``assets.lock.json``.

    python scripts/sync_assets.py --from ../framework --ref v2.0.0   # copy at a tag
    python scripts/sync_assets.py --from ../framework                # copy the working tree
    python scripts/sync_assets.py --check                            # copies match the lock
    python scripts/sync_assets.py --check --from ../framework        # ...and match the source
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "assets.lock.json"
WORKTREE = "worktree"

# Source path or glob in the R repository -> destination folder in framework-py.
# Runtime files go in the package; test-only files go in tests/shared.
ASSETS: dict[str, str] = {
    "inst/schema/*.json": "src/framework/_assets/schema",
    # The docs site format (docs/site, scripts/build_docs.py).
    "inst/schema/docs-site.md": "docs",
    "inst/templates/init.sql": "src/framework/_assets",
    "inst/hooks/pre-commit": "src/framework/_assets/hooks",
    # Framework's tools and its skill and package sets, canonical in R.
    "inst/features/*.json": "src/framework/_assets/features",
    "inst/sets/skills/*.json": "src/framework/_assets/sets/skills",
    "inst/sets/packages/*.json": "src/framework/_assets/sets/packages",
    # Workflows: how notebooks are written (quarto, jupyter, or scripts).
    "inst/workflows/*.json": "src/framework/_assets/workflows",
    # How adopt() maps an existing project's folders to directory keys.
    "inst/adopt/rules.json": "src/framework/_assets/adopt",
    "inst/conformance/README.md": "tests/shared/conformance",
    "inst/conformance/merge/*.json": "tests/shared/conformance/merge",
    "inst/conformance/create/python/*.json": "tests/shared/conformance/create/python",
    "inst/conformance/adopt/*.json": "tests/shared/conformance/adopt",
    "NAMESPACE": "tests/shared",
    # The servers for tests/integration, with the seed script for each.
    "tests/integration/compose.yml": "tests/integration",
    "tests/integration/postgres/init.sql": "tests/integration/postgres",
    "tests/integration/mysql/init.sql": "tests/integration/mysql",
    "tests/integration/mariadb/init.sql": "tests/integration/mariadb",
    "tests/integration/sqlserver/init.sql": "tests/integration/sqlserver",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def list_sources(repo: Path, ref: str) -> list[str]:
    """Source paths matching ASSETS, relative to the R repository root."""
    if ref == WORKTREE:
        found = [
            path.relative_to(repo).as_posix()
            for pattern in ASSETS
            for path in sorted(repo.glob(pattern))
        ]
    else:
        tracked = git(repo, "ls-tree", "-r", "--name-only", ref).decode().splitlines()
        found = [
            name for pattern in ASSETS for name in tracked if PurePosixPath(name).match(pattern)
        ]
    if not found:
        sys.exit(f"No shared files found in {repo} at {ref}")
    return found


def read_source(repo: Path, ref: str, name: str) -> bytes:
    if ref == WORKTREE:
        return (repo / name).read_bytes()
    return git(repo, "show", f"{ref}:{name}")


def destination(name: str) -> str:
    for pattern, dest in ASSETS.items():
        if PurePosixPath(name).match(pattern):
            return f"{dest}/{PurePosixPath(name).name}"
    raise KeyError(name)


def sync(repo: Path, ref: str) -> None:
    files: dict[str, dict[str, str]] = {}
    for name in list_sources(repo, ref):
        data = read_source(repo, ref, name)
        dest = destination(name)
        target = ROOT / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[dest] = {"source": name, "sha256": sha256(data)}

    stale = {entry for entry in load_lock().get("files", {}) if entry not in files}
    for dest in stale:
        (ROOT / dest).unlink(missing_ok=True)

    commit = git(repo, "rev-parse", "HEAD" if ref == WORKTREE else ref).decode().strip()
    lock = {"source": "framework (R package)", "ref": ref, "commit": commit, "files": files}
    LOCK.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Copied {len(files)} files from {repo} at {ref}")


def load_lock() -> dict[str, Any]:
    if not LOCK.exists():
        return {}
    data: dict[str, Any] = json.loads(LOCK.read_text(encoding="utf-8"))
    return data


def check(repo: Path | None) -> int:
    lock = load_lock()
    files = lock.get("files")
    if not isinstance(files, dict) or not files:
        print("assets.lock.json is missing or empty. Run sync_assets.py --from <R repo>.")
        return 1

    problems: list[str] = []
    for dest, entry in sorted(files.items()):
        path = ROOT / dest
        if not path.exists():
            problems.append(f"missing: {dest}")
        elif sha256(path.read_bytes()) != entry["sha256"]:
            problems.append(f"edited locally: {dest} (change it in the R repository and re-sync)")

    ref = str(lock.get("ref"))
    if repo is not None:
        if ref == WORKTREE:
            print(
                f"Note: the copies came from an uncommitted R working tree ({lock.get('commit')})."
            )
            ref_to_check = WORKTREE
        else:
            ref_to_check = ref
        expected = {destination(name): name for name in list_sources(repo, ref_to_check)}
        for dest in sorted(set(expected) - set(files)):
            problems.append(f"new in R at {ref_to_check}, not copied: {expected[dest]}")
        for dest, entry in sorted(files.items()):
            if dest not in expected:
                problems.append(f"no longer in R at {ref_to_check}: {entry['source']}")
                continue
            data = read_source(repo, ref_to_check, expected[dest])
            if sha256(data) != entry["sha256"]:
                problems.append(f"differs from R at {ref_to_check}: {entry['source']}")

    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"{len(files)} shared files match assets.lock.json (R ref: {ref})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="repo", type=Path, help="path to the R repository")
    parser.add_argument(
        "--ref", default=WORKTREE, help="git ref to copy from (default: working tree)"
    )
    parser.add_argument("--check", action="store_true", help="verify instead of copying")
    args = parser.parse_args()

    repo = args.repo.resolve() if args.repo else None
    if args.check:
        return check(repo)
    if repo is None:
        parser.error("--from is required when copying")
    sync(repo, args.ref)
    return 0


if __name__ == "__main__":
    sys.exit(main())
