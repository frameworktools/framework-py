---
id: project-types
title: Project types
description: Directory layouts, .gitignore rules, and workflows for standard, privacy-sensitive, course, presentation, bare, Shiny, and FastAPI projects
section: project_types
position: 10
---

## Overview

Framework provides five local project types plus two web application blueprints on Framework Cloud. Each type configures directory paths, `.gitignore` rules, Quarto render directories, and AI assistant skills.

| Project type | `type` argument | Typical workload |
|--------------|-----------------|------------------|
| Standard project | `project` | Data analysis, research pipelines, and recurring reports |
| Privacy-sensitive project | `project_sensitive` | Research with restricted or identifiable data separated from public outputs |
| Course | `course` | Lectures, slides, assignments, readings, and course modules |
| Presentation | `presentation` | Standalone Quarto RevealJS talk or slide deck |
| Bare | `bare` | Minimal `settings.yml` and `pyproject.toml` for custom layouts |

Pass `type` to [new_project()](ref:new_project) or `python -m framework create`:

```python
import framework as fw

fw.new_project("Sales Analysis", "~/code/sales-analysis", type="project")
fw.new_project("Patient Outcomes", "~/code/patient-outcomes", type="project_sensitive")
fw.new_project("Data Science 101", "~/teaching/ds101", type="course")
fw.new_project("Q4 Report", "~/talks/q4-report", type="presentation")
```

```sh
python -m framework create --name "Sales Analysis" --path ~/code/sales-analysis --type project
```

### Framework Cloud app blueprints

Two additional application types resolve as built-in Framework Cloud blueprints after signing in with [cloud_login()](ref:cloud_login):

| Project type | `type` argument | Blueprint handle | Typical workload |
|--------------|-----------------|------------------|------------------|
| [Shiny for Python](guide:shiny-app) | `shiny` or `shiny_python` | `framework/shiny_python` | Interactive web application |
| [FastAPI app](guide:fastapi-app) | `fastapi` | `framework/fastapi` | HTTP JSON API service |

```python
fw.cloud_login()
fw.new_project("My App", "~/code/my-app", type="shiny")
fw.new_project("My API", "~/code/my-api", type="fastapi")
```

In Python, `type="shiny"` creates a Shiny for Python app; R's `plumber` type is R-only (`type="fastapi"` is the Python API blueprint).

## Shared project files

Every non-`bare` project includes:

- `settings.yml` (`language: python`)
- `pyproject.toml` with `framework-py` and active tool dependencies
- `scaffold.py` startup hook
- `.env` credential placeholders (gitignored)
- `framework.db` SQLite database (gitignored)
- Root `_quarto.yml` (when the `quarto` tool is enabled)
- `AGENTS.md`, Framework skills, and `CLAUDE.md` (when `claude` is enabled)
- `<slug>.code-workspace` for VS Code and Positron

All `.gitignore` templates exclude `private` directories, `.env` files, `cache/`, `scratch/`, `logs/`, `framework.db`, Quarto intermediates, and `.venv/`. See [Git](guide:git).

## Standard project

`project` is the default layout for data analysis, separating staged inputs from typed outputs:

```text
sales-analysis/
├── docs/                  # renders to outputs/docs/
├── functions/             # reusable Python modules added to sys.path
├── inputs/
│   ├── raw/               # unmodified source files
│   ├── intermediate/      # cleaned and joined intermediates
│   └── final/             # analysis-ready datasets
├── logs/
├── notebooks/             # Jupyter and Quarto notebooks
├── outputs/
│   └── notebooks/         # rendered notebook HTML
├── scratch/
├── scripts/
├── settings/              # split YAML configuration files
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

`outputs/tables/`, `outputs/figures/`, `outputs/models/`, `outputs/reports/`, and `outputs/cache/` are created on first save. The standard `.gitignore` does not ignore `inputs/` or `outputs/`; add ignore rules for large files, or choose `project_sensitive` whenever restricted records are involved.

## Privacy-sensitive project

`project_sensitive` isolates restricted records (such as PHI or PII) from shareable outputs by splitting every input and output directory into `private/` and `public/` trees:

```text
patient-outcomes/
├── docs/
├── functions/
├── inputs/
│   ├── private/           # restricted data, gitignored
│   │   ├── raw/
│   │   ├── intermediate/
│   │   └── final/
│   └── public/            # de-identified or public reference data
│       ├── raw/
│       ├── intermediate/
│       └── final/
├── logs/
├── notebooks/
├── outputs/
│   └── private/
│       └── notebooks/     # rendered notebooks (gitignored)
├── scratch/
├── scripts/
├── settings/
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

`outputs/private/<kind>/` and `outputs/public/<kind>/` create on first save, and caches live in `outputs/private/cache/`.

### Built-in safeguards

- **Git:** `.gitignore` excludes `/inputs/private/`, `/outputs/private/`, and all `private` or `sensitive` folders while preserving `README.md` and `.gitkeep`.
- **Pre-commit audit:** `data_security` and `check_sensitive_dirs` hooks start enabled and install automatically at repository creation (or via [bootstrap()](ref:bootstrap) / [git_hooks_install()](ref:git_hooks_install)).
- **Outputs:** `save_*()` functions default to `private/` unless `public=True` is passed, warning if the session previously read private data.
- **Publishing:** all `publish*()` functions warn and require confirmation before uploading from `private/` or sensitive projects.
- **AI assistants:** `AGENTS.md` and `framework-sensitive-data` forbid reading restricted paths, and `.claude/settings.json` enforces tool and sandbox read blocks on `private/` and `.env`.

### Restricted-data workflow

Register raw files under `inputs.private.raw.*` with `locked=True` and pass `public=True` only after aggregating and suppressing small cells:

```python
import framework as fw

fw.scaffold()
fw.data_add("inputs/private/raw/patients.csv", name="inputs.private.raw.patients", locked=True)

patients = fw.data_read("inputs.private.raw.patients")
by_region = patients.groupby(["age_group", "region"]).size().reset_index(name="n")
by_region = by_region[by_region["n"] >= 11]

fw.save_table(by_region, "patients_by_region", public=True)
```

## Course

`course` organizes teaching materials with inline `settings.yml` and Quarto output routed to gitignored `rendered/`:

```text
data-science-101/
├── assignments/           # renders to rendered/assignments/
├── cache/
├── course_docs/           # renders to rendered/course_docs/
├── data/                  # shared course datasets
├── modules/               # default make_notebook() target; renders to rendered/modules/
├── readings/
├── slides/                # RevealJS lectures; renders to rendered/slides/
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

Create lectures and assignments with `dir` and `subdir`:

```python
fw.make_presentation("Introduction to Python", dir="slides", subdir="week-01")
fw.make_notebook("assignment-01", dir="assignments")
fw.make_notebook("syllabus", dir="course_docs")
```

## Presentation

`presentation` provides a lightweight single-deck layout with inline `settings.yml` and a RevealJS `presentation.qmd` at the root:

```text
q4-report/
├── cache/
├── data/
├── presentation.qmd
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

Render in place with Quarto:

```sh
uv run quarto render presentation.qmd
```

## Bare project

`bare` creates only `settings.yml`, `pyproject.toml`, `.gitignore`, and `.framework/spec.json`. Add custom directories incrementally with [project_add_directory()](ref:project_add_directory):

```python
fw.project_add_directory(".", "interviews", "Interview transcripts", "inputs/interviews")
```

To migrate an existing standard project to the privacy-sensitive layout later, run `fw.upgrade(path, type="project_sensitive", dry_run=False)`.
