---
id: project-types
title: Project Types
description: Standard, privacy-sensitive, course, and presentation projects, with their folders, .gitignore rules, and uses
section: project_types
position: 10
---

## Overview

Framework creates four kinds of project, plus a bare project with no structure. Each type sets the folders, the `.gitignore`, the render folders for Quarto, and the skills installed for AI assistants.

| Type | `type` value | Use it for |
|------|--------------|------------|
| Standard project | `project` | Data analysis, research projects, pipelines, and reports |
| Privacy-sensitive project | `project_sensitive` | Research with restricted data that must stay separate from shareable results |
| Course | `course` | Teaching materials: slides, assignments, readings, and course documents |
| Presentation | `presentation` | A single talk or slide deck |
| Bare | `bare` | A settings file and `pyproject.toml` only, for building your own structure |

Pass the type to [new()](ref:new) or to the command line.

```python
import framework as fw

fw.new("Sales Analysis", "~/projects/sales-analysis", type="project")
fw.new("Patient Outcomes", "~/projects/patient-outcomes", type="project_sensitive")
fw.new("Data Science 101", "~/teaching/ds101", type="course")
fw.new("Q4 Report", "~/talks/q4-report", type="presentation")
```

```sh
python -m framework create --name "Sales Analysis" --path ~/projects/sales-analysis --type project
```

Without a type, an interactive session asks for one, and a script creates a bare project.

## What Every Type Shares

Every type except bare includes these files.

- `settings.yml`, with `language: python`.
- `pyproject.toml`, which depends on `framework-py`.
- `scaffold.py`, which [scaffold()](ref:scaffold) runs.
- `.env` with empty credential placeholders, ignored by git.
- `framework.db`, ignored by git.
- `_quarto.yml` files for rendering.
- `AGENTS.md`, the Framework skills, and `CLAUDE.md` when Claude Code is selected.
- A `.code-workspace` file for VS Code and Positron.

All four `.gitignore` templates ignore folders named `private` anywhere in the project, `.env` files, `cache/`, `scratch/`, and `logs/` folders, `framework.db`, Quarto's working files, and Python environments. The [Git](guide:git) guide lists the rules.

Folder names come from the package defaults and can be changed per type in your global settings, or per project in `settings.yml`.

## Standard Project

The standard project is the default for data analysis. It separates inputs by stage, keeps outputs by kind, and has folders for notebooks, scripts, and reusable functions.

```text
sales-analysis/
├── docs/                  # project documentation (renders to outputs/docs/)
├── functions/             # reusable modules, on the import path after scaffold()
├── inputs/
│   ├── raw/               # data as received
│   ├── intermediate/      # cleaned and joined data
│   └── final/             # analysis-ready datasets
├── logs/
├── notebooks/             # Quarto and Jupyter notebooks
├── outputs/
│   └── notebooks/         # rendered notebooks
├── scratch/
├── scripts/
├── settings/              # split settings files
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

These folders are created the first time something is saved in them.

- `outputs/tables/`, `outputs/figures/`, `outputs/models/`, and `outputs/reports/`, by the `save_*()` functions.
- `outputs/cache/`, by the cache.

The standard `.gitignore` does not ignore `inputs/` or `outputs/`. Decide which data and results may be committed, and add rules for the rest. When a project holds data that must never be committed, use the privacy-sensitive type.

Use a standard project for most analysis work: research projects, data pipelines, exploratory analysis, and recurring reports.

## Privacy-Sensitive Project

The privacy-sensitive project is for research with restricted data, such as protected health information. It splits every input stage and every kind of output into private and public folders.

```text
patient-outcomes/
├── docs/
├── functions/
├── inputs/
│   ├── private/           # restricted data, never committed
│   │   ├── raw/
│   │   ├── intermediate/
│   │   └── final/
│   └── public/            # de-identified or aggregated data
│       ├── raw/
│       ├── intermediate/
│       └── final/
├── logs/
├── notebooks/
├── outputs/
│   └── private/
│       └── notebooks/     # rendered notebooks, which can show restricted data
├── scratch/
├── scripts/
├── settings/
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

The output folders follow the same pattern. `outputs/private/tables/` and `outputs/public/tables/`, with matching pairs for figures, models, reports, and notebooks, are created when something is first saved there. The cache lives in `outputs/private/cache/`.

### Protections

Several parts of Framework treat the private folders as restricted.

- **Git.** The `.gitignore` ignores `/inputs/private/`, `/outputs/private/`, and every folder named `private` or `sensitive`. It keeps `README.md` and `.gitkeep` files, so the folder structure can be documented.
- **Outputs.** The `save_*()` functions save to private folders unless you pass `public=True`.
- **Publishing.** Every publishing function refuses files inside a `private` folder.
- **AI assistants.** `AGENTS.md` tells assistants not to read the private folders. When Claude Code is selected, `.claude/settings.json` denies it access to them and to `.env`. The `framework-sensitive-data` skill is installed.
- **Settings.** `security: sensitive: true` marks the project.
- **Audit.** [git_security_audit()](ref:git_security_audit) passes on a new privacy-sensitive project. Turning on the `data_security` hook check runs it before every commit.

```python
fw.git_hooks_enable("data_security")
```

### Working with Private and Public Data

Restricted data goes into the private folders, and only aggregated or de-identified results move to the public ones. Register private files under four-part names with [data_add()](ref:data_add). The entries are locked by default, so a changed source file is caught.

```python
import framework as fw

fw.scaffold()
fw.data_add("inputs/private/raw/patients.csv", name="inputs.private.raw.patients")

patients = fw.data_read("inputs.private.raw.patients")

by_region = patients.groupby(["age_group", "region"]).size().reset_index(name="n")
by_region = by_region[by_region["n"] >= 11]

fw.save_table(by_region, "patients_by_region", public=True)
```

Check `git status` before each commit, and review any file before it moves from a private folder to a public one.

## Course

The course type organizes teaching materials. It has folders for slides, assignments, readings, course documents, modules, and shared data.

```text
data-science-101/
├── assignments/           # renders to rendered/assignments/
├── cache/
├── course_docs/           # syllabus and schedules, render to rendered/course_docs/
├── data/                  # datasets shared across the course
├── modules/               # notebooks, render to rendered/modules/
├── readings/
├── slides/                # reveal.js lectures, render to rendered/slides/
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

In a course, `directories.notebooks` points at `modules/`, so [make_notebook()](ref:make_notebook) creates files there by default. Slides render as reveal.js presentations and the other folders as HTML. Course settings are kept inline in `settings.yml`, without split files.

Create materials with the `dir` and `subdir` arguments. An explicit `dir` is relative to the current folder, so run these calls from the project root.

```python
fw.make_presentation("Introduction to Python", dir="slides", subdir="week-01")
fw.make_notebook("assignment-01", dir="assignments")
fw.make_notebook("syllabus", dir="course_docs")
```

The course `.gitignore` adds a rule for `/slides/_rendered/`. Rendered output in `rendered/` is not ignored, so you can commit it to share it with students, or add a rule to keep it out of git. Student data belongs in a folder named `private`, which every template ignores.

## Presentation

The presentation type is the smallest structure, for a single talk or slide deck.

```text
q4-report/
├── cache/
├── data/
├── presentation.qmd       # the slides, in reveal.js format
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

`presentation.qmd` starts with the reveal.js stub, titled with the project name. The root `_quarto.yml` renders it as reveal.js, and the output is written next to the source.

```sh
uv run quarto render presentation.qmd
```

Change the `format` in the front matter to produce another kind of document, such as `html`, `pdf`, or `docx`. Presentation settings are kept inline in `settings.yml`.

Use a presentation project for conference talks, committee presentations, and one-time briefings. For ongoing work with several deliverables, use a standard project.

## Bare Project

A bare project has only `settings.yml`, `pyproject.toml`, `.gitignore`, and `.framework/spec.json`. It has no folders, no `.env`, no AI files, and no `framework.db` until [scaffold()](ref:scaffold) creates one. Use it as a starting point when none of the other types fits, and add folders with [project_add_directory()](ref:project_add_directory).

```python
fw.project_add_directory(".", "interviews", "Interview transcripts", "inputs/interviews")
```

## Choosing a Type

- Choose a **standard project** for analysis work whose data may be committed or kept out of git with ordinary rules.
- Choose a **privacy-sensitive project** when any data must never leave the machine or the secure environment, or when a reviewer needs to see a clear boundary between restricted and shareable material.
- Choose a **course** for a set of lectures, assignments, and readings.
- Choose a **presentation** for one deck.

A standard project can also be marked sensitive with `security: sensitive: true` in a framework.pub blueprint, which gives it the privacy-sensitive `.gitignore` and the `framework-sensitive-data` skill. The type of an existing project is recorded in `project_type` and is not converted automatically, so choose the type when you create the project.
