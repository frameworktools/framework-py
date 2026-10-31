---
id: git
title: Git
description: The .gitignore templates, the shared pre-commit hook, git_hooks_*(), and git_security_audit()
section: features
position: 12
---

## Overview

Framework sets up git for new projects and guards against committing restricted data and secrets. It writes a `.gitignore` for the project type, installs a pre-commit hook when you enable its checks, and audits a repository on request. For everyday version control, use git itself or your editor. framework-py does not wrap git commands such as `status`, `add`, `commit`, or `push`.

## New Repositories

[new()](ref:new) initializes a git repository when git is installed and `defaults.git.initialize` in your global settings is not `false`. It then takes these steps.

1. It installs the pre-commit hook when a hook check is enabled.
2. It makes sure `.gitignore` ignores `.env`. If git would still track `.env`, creation stops with an error before anything is committed.
3. It stages every file and makes the first commit, `Initial commit from Framework`. When git's `user.name` or `user.email` is not set, it skips the commit and prints a note.

## .gitignore Templates

Each project type gets its own `.gitignore`. All of them ignore the same core set of files.

- Folders named `private` or ending in `_private`, anywhere in the project.
- `.env` and its variants, `credentials/` and `secrets/` folders, and key files (`*.key` and `*.pem`).
- `cache/`, `scratch/`, `tmp/`, `temp/`, and `logs/` folders, anywhere in the project.
- `framework.db` and its `-wal` and `-shm` files.
- Quarto's working files (`.quarto/`, `_freeze/`, `*_cache/`, and `*_files/`).
- Python environments and caches (`.venv/`, `__pycache__/`, `.ipynb_checkpoints/`, and the pytest, mypy, and ruff caches).
- Personal editor history and operating system files.

The privacy-sensitive template adds explicit rules for `/inputs/private/`, `/outputs/private/`, and `/reference/private/`, ignores folders named `sensitive`, and also ignores `*.p12` and `*.pfx` files. It re-includes `README.md` and `.gitkeep` files under `inputs/`, `outputs/`, and `reference/`, so a private folder's structure can be documented. A standard project with `security: sensitive: true` uses the same template.

The `.gitignore` belongs to the project after creation. Edit it as the project needs. [bootstrap()](ref:bootstrap) adds a `.env` rule back when the file no longer ignores `.env`.

## The Pre-commit Hook

R, Python, and the `fw` tool install the same pre-commit hook. It is a short POSIX shell script that finds a runtime and runs the checks in that language's package. For a Python project, which it recognizes by `pyproject.toml`, `uv.lock`, or `.venv`, it runs `python -m framework hook pre-commit` with the first Python it finds in this order.

1. `.venv/bin/python`, or `.venv/Scripts/python.exe` on Windows.
2. `uv run --frozen python`, when the project has `uv.lock` and uv is installed.
3. `python3` on the `PATH`.

The chosen Python must be able to import framework-py, so run `uv sync` after cloning. When no runtime is found, the commit fails, because the hook guards against committing restricted data. `git commit --no-verify` skips the hook for a commit you know is safe.

### Hook Checks

The checks are turned on in the `git` section of the settings, which standard and privacy-sensitive projects keep in `settings/git.yml`.

```yaml
git:
  hooks:
    data_security: true
    check_sensitive_dirs: true
```

| Check | Effect |
|-------|--------|
| `data_security` | Runs [git_security_audit()](ref:git_security_audit) and fails the commit when a check fails. |
| `check_sensitive_dirs` | Warns about folders whose names contain `private`, `confidential`, `sensitive`, `cache`, or `scratch` and that git does not ignore. At a terminal, it asks before committing. |

Both checks are off by default. The older name `warn_unignored_sensitive` is accepted for `check_sensitive_dirs`. The R package's `ai_sync` check was removed, because projects keep AI instructions in `AGENTS.md` and skills, and an `ai_sync` setting is ignored.

### Managing the Hook

These functions read and write the `hooks` settings and install or remove the hook.

- [git_hooks_enable()](ref:git_hooks_enable) turns on one check and installs the hook.
- [git_hooks_disable()](ref:git_hooks_disable) turns off one check, and removes the hook when no check is left.
- [git_hooks_install()](ref:git_hooks_install) installs the hook when a check is enabled.
- [git_hooks_uninstall()](ref:git_hooks_uninstall) removes the hook.
- [git_hooks_list()](ref:git_hooks_list) returns each check and whether it is enabled. Its `attrs["installed"]` says whether the hook is installed.

```python
import framework as fw

fw.git_hooks_enable("data_security")
fw.git_hooks_list()
fw.git_hooks_disable("data_security")
```

A pre-commit hook that Framework did not write is kept. `git_hooks_install(force=True)` replaces it. A hook written by R or by an earlier version of Framework is replaced without `force`.

[bootstrap()](ref:bootstrap) installs the hook in a cloned project when a check is enabled, because git does not carry hooks with a clone.

## The Security Audit

[git_security_audit()](ref:git_security_audit) checks that private data and secrets stay out of the repository. It runs five checks.

| Check | Fails when |
|-------|------------|
| `gitignore_coverage` | A private data folder, or a data file in one, is not ignored |
| `private_data_exposure` | Git tracks a file in a private data folder |
| `git_history` | A past commit added, changed, or deleted a data file in a private data folder |
| `secrets_exposure` | Git tracks a `.env` file or a variant such as `.env.local`. Files ending in `.example`, `.template`, or `.sample` are allowed. |
| `orphaned_files` | Warns, without failing, about data files outside the configured folders |

```python
result = fw.git_security_audit()
result.passed
result.summary
result.findings["gitignore_coverage"]
```

The audit returns an `AuditResult`. `passed` is `False` when any check failed, and warnings still pass. `findings` holds a DataFrame per check, and `recommendations` lists what to do. The result is also recorded in the `meta` table of `framework.db`.

The arguments adjust the audit.

- `check_git_history=False` skips the history scan.
- `history_depth` limits the scan to `"shallow"` (the last 100 commits) or a number of commits. The default is `"all"`.
- `auto_fix=True` adds unignored private folders to `.gitignore`. Files git already tracks stay tracked until you run `git rm --cached`.
- `extensions` lists the file extensions that count as data.
- `verbose=False` skips the printed report.

### Which Folders Are Private

The audit treats a folder in `directories` as private when its key contains `private`, when its path has a segment whose name contains `private`, `confidential`, or `sensitive`, or when its key is `cache`, `scratch`, or `logs`. Folder keys that contain `_public` are exempt. These are the folders every project type's `.gitignore` ignores, so new standard and privacy-sensitive projects pass the audit.

Other input and output folders, such as `inputs/raw` and `outputs/tables` in a standard project, are not private, and git commits their files unless you ignore them. Data that must stay out of the repository belongs in a private folder.

The R package's audit also treats every `inputs_*` and `outputs_*` folder as private, so in R the audit fails for a standard project whose `.gitignore` does not ignore those folders.
