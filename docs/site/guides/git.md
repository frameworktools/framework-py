---
id: git
title: Git
description: Repository initialization, .gitignore templates, pre-commit hooks, and git_security_audit()
section: features
position: 12
---

## Overview

Framework configures git repositories for new projects and guards against committing restricted data, notebook outputs, and credentials. It generates project-type `.gitignore` templates, manages a cross-language pre-commit hook, and runs repository security audits via [git_security_audit()](ref:git_security_audit). Run everyday `git` commands (`status`, `add`, `commit`, `push`) directly in your terminal or editor.

## Repository initialization

When `git` is installed and `defaults.git.initialize` is not `false`, [new_project()](ref:new_project):

1. Verifies that `.gitignore` excludes `.env`, aborting before any commit if git would track `.env`.
2. Stages generated files and creates `Initial commit from Framework` when git `user.name` and `user.email` are configured (skipping the commit with a note otherwise).
3. Installs `.git/hooks/pre-commit` when at least one hook check is enabled.

Projects created inside an existing parent git worktree skip `git init` and the initial commit.

## .gitignore templates

Every project type's `.gitignore` excludes:

- Any directory named `private` or ending in `_private`
- `.env` variants, `credentials/`, `secrets/`, `*.key`, and `*.pem`
- `cache/`, `scratch/`, `tmp/`, `temp/`, and `logs/` directories
- `framework.db`, `framework.db-wal`, and `framework.db-shm`
- Quarto working directories (`.quarto/`, `_freeze/`, `*_cache/`, `*_files/`)
- Python virtual environments and tool caches (`.venv/`, `__pycache__/`, `.ipynb_checkpoints/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`)
- OS and editor scratch files

Privacy-sensitive projects (`project_sensitive` or `security.sensitive: true`) additionally ignore `/inputs/private/`, `/outputs/private/`, `/reference/private/`, directories named `sensitive`, and `*.p12`/`*.pfx` certificates, while re-including `README.md` and `.gitkeep` placeholders under `inputs/`, `outputs/`, and `reference/`.

## Pre-commit hook

R, Python, and `fw` install the same POSIX shell pre-commit hook. In a Python project (detected via `pyproject.toml`, `uv.lock`, or `.venv`), the hook invokes `python -m framework hook pre-commit` using the first available runtime:

1. `.venv/bin/python` (`.venv/Scripts/python.exe` on Windows).
2. `uv run --frozen python` when `uv.lock` and `uv` exist.
3. `python3` on `PATH`.

Run `uv sync` after cloning so `framework-py` is importable. If no runtime is available, the hook fails closed; use `git commit --no-verify` only for verified commits.

### Hook checks

Configure checks under `git.hooks` (in `settings/git.yml` for standard and sensitive projects):

```yaml
git:
  hooks:
    data_security: true
    check_sensitive_dirs: true
    check_notebook_outputs: true
```

| Check | Behavior |
|-------|----------|
| `data_security` | Runs [git_security_audit()](ref:git_security_audit) and blocks commits on failures (`gitignore_coverage`, `private_data_exposure`, `git_history`, `secrets_exposure`, `connection_secrets`). Prints non-blocking warnings (`published_outputs`, `identifier_columns`, untracked `connection_secrets`) and lets the commit proceed. |
| `check_sensitive_dirs` | Warns when unignored directories contain `private`, `confidential`, `sensitive`, `cache`, or `scratch` in their name (excluding installed skill folders) and prompts at a terminal before committing. |
| `check_notebook_outputs` | Blocks staged `.ipynb` files that retain cell outputs or execution counts, printing instructions to clear outputs. |

`project_sensitive` projects enable `data_security` and `check_sensitive_dirs` by default at creation time; other project types start with all three checks disabled.

### Hook management

- [git_hooks_enable()](ref:git_hooks_enable) enables a check and installs `.git/hooks/pre-commit`.
- [git_hooks_disable()](ref:git_hooks_disable) disables a check and removes the hook when none remain enabled.
- [git_hooks_install()](ref:git_hooks_install) installs the hook (`force=True` replaces third-party hooks; Framework hooks update automatically).
- [git_hooks_uninstall()](ref:git_hooks_uninstall) removes the hook.
- [git_hooks_list()](ref:git_hooks_list) returns check states (`df.attrs["installed"]` reports hook installation).

```python
import framework as fw

fw.git_hooks_enable("data_security")
fw.git_hooks_list()
fw.git_hooks_disable("data_security")
```

[bootstrap()](ref:bootstrap) reinstalls enabled hooks automatically after `git clone`.

## Security audit

[git_security_audit()](ref:git_security_audit) runs eight repository checks and returns an `AuditResult`:

| Check | Severity and condition |
|-------|------------------------|
| `gitignore_coverage` | **Fails** when a private directory or data file inside one is not ignored by git (`git check-ignore --no-index`). |
| `private_data_exposure` | **Fails** when git tracks a file inside a private directory (allowing empty `.gitkeep`/`.keep` or `.gitignore` ≤ 4 KB). |
| `git_history` | **Fails** when past commits touched data files inside private or inputs directories, or data files whose headers contain identifier-like columns. |
| `secrets_exposure` | **Fails** when git tracks `.env` or `.env.*` files (excluding `.example`, `.template`, and `.sample`). |
| `connection_secrets` | **Fails** when a git-tracked settings file contains literal credentials or non-empty `env("VAR", "fallback")` defaults under credential keys (`password`, `secret`, `token`, `access_key`, `api_key`, `private_key`, `credential`); **warns** when untracked. |
| `orphaned_files` | **Warns** on unignored data files outside configured project directories. |
| `published_outputs` | **Warns** on unignored files in local folder publishing destinations (exempting `visibility: public` folders in non-sensitive projects). |
| `identifier_columns` | **Warns** when git-tracked CSV, TSV, Parquet, Stata, or SPSS headers outside private folders match direct identifier names (see [Data management](guide:data-management)). |

Mark verified public lookup tables or synthetic datasets under `security.reviewed` (paths, folders, or globs) to exempt them from `git_history`, `private_data_exposure`, and `identifier_columns`:

```yaml
security:
  sensitive: true
  reviewed:
    - inputs/public/
    - "**/*_synthetic_*.csv"
```

```python
result = fw.git_security_audit()
result.passed
result.summary
result.findings["gitignore_coverage"]
```

Audit options: `check_git_history=False` skips commit history, `history_depth="shallow"` scans the last 100 commits (or pass an integer), `auto_fix=True` appends missing `.gitignore` entries, `extensions=[...]` overrides audited file extensions, and `verbose=False` suppresses console output.

### Private directory rules

In `framework-py`, the audit treats a configured directory as private when its key contains `private` (excluding `_public`), its path has a segment containing `private`, `confidential`, or `sensitive`, or its key is `cache`, `scratch`, or `logs`. Standard `inputs/raw` and `outputs/tables` directories remain committable unless ignored. (By contrast, the R package's audit treats all `inputs_*` and `outputs_*` directories as private unless exempted.)
