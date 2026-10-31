---
id: new-project
title: Making a New Project
description: Creating a project with fw.new() or the command line, and preparing a cloned project with bootstrap()
section: getting_started
position: 5
---

## Create a Project from Python

[new()](ref:new) creates a Python project from your global defaults. It takes a name, the folder to create, and a project type.

```python
import framework as fw

fw.new("Vaccine Study", "~/projects/vaccine-study", type="project")
```

The type is one of `project`, `project_sensitive`, `course`, `presentation`, or `bare`. The [Project Types](guide:project-types) guide describes each one.

```python
fw.new("Patient Outcomes", "~/projects/patient-outcomes", type="project_sensitive")
fw.new("Stats 101", "~/teaching/stats-101", type="course")
fw.new("Q4 Review", "~/talks/q4-review", type="presentation")
```

In an interactive session, `new()` asks for any value you leave out. It suggests `~/projects/<name>` as the location and `bare` as the type. In a script or notebook, where standard input is not a terminal, a missing name or location is an error and a missing type means `bare`. Pass `interactive=True` or `interactive=False` to choose the behavior yourself.

The folder must not exist yet. `new()` returns the path of the new project.

### Tools and Sets

Tools are switches for what a project uses beyond its folders. Each tool adds only its own files, folders, and packages. [tools_available()](ref:tools_available) lists the tools for Python projects.

- `uv` manages the project's dependencies in `pyproject.toml` and `uv.lock`. It is on for every project.
- `quarto` writes the `_quarto.yml` files and render settings. It is on for every type except `bare`.
- `jupyter` adds the `ipykernel` package for Jupyter notebooks. It is off unless you turn it on.
- `quarto_slides` adds a `slides/` folder that renders as RevealJS.
- `quarto_manuscript` adds a `manuscript/` folder with a Quarto manuscript to start from.

Pass `tools` as a list of tools to turn on, or as a map that can also turn a default off.

```python
fw.new(
    "Lab Notes", "~/projects/lab-notes", type="project", tools={"quarto": False, "jupyter": True}
)
fw.new("Q4 Review", "~/talks/q4-review", type="project", tools=["quarto_slides"])
```

A tool that another tool needs is turned on too, with a message. `quarto_slides` and `quarto_manuscript` turn on `quarto`. Turning off a tool that another one needs is an error. The project's tools are recorded under `tools` in `settings.yml`. A project without a `tools` section predates it and keeps using Quarto.

`skill_sets` and `package_sets` add bundled sets of AI skills and packages. The `framework` skill set adds Framework's four skills. The tool and set definitions are shared with the R package and Framework Cloud, so a project gets the same pieces whichever one creates it.

With the command line, `--tool ID` turns a tool on and `--tool ID=false` turns it off. `--skill-set` and `--package-set` add sets. Each option can be repeated.

## Create a Project from the Command Line

The package's command-line entry point runs `new()` with the same defaults.

```sh
python -m framework create --name "Vaccine Study" --path ~/projects/vaccine-study --type project
```

Without `--type`, the project is `bare`. With `--interactive`, the command asks for missing answers. The command prints the project folder on success and exits with status 1 and a message on failure.

When framework-py is not installed in any environment yet, uv can run the command with a temporary copy of the package.

```sh
uv run --with framework-py python -m framework create --name "Vaccine Study" --path ~/projects/vaccine-study --type project
```

### With fw

The `fw` command-line tool creates projects in every Framework language. For a Python project, it finds a Python runtime with framework-py and runs the command above.

```sh
fw new vaccine-study --lang python --type project
```

`fw` creates the project in `./<name>` unless `--dir` names another folder.

### From a Layers File

framework.pub describes a project as a layers file. `--from-layers` creates a project from one.

```sh
python -m framework create --from-layers layers.json --path .
```

This form runs headless unless you pass `--interactive`. A headless run asks no questions, initializes no git repository, writes nothing under `.github/workflows/`, and does not register the project. It works in a fresh checkout that holds only `.git`, `.github`, `.framework`, `README.md`, and `LICENSE`, which is how the framework.pub GitHub Action uses it. [project_create_from_layers()](ref:project_create_from_layers) does the same from Python.

## Create a Project from Framework Cloud

A project defined on Framework Cloud is created from its project token.

```python
fw.new(key="fw_...")
```

`new()` fetches the project's definition from the server and saves the token and server in the new project's `.env` as `FW_PROJECT_TOKEN` and `FW_CLOUD_URL`. The project then publishes to that server and uses its integrity ledger. The [Framework Cloud](guide:cloud) guide describes what the server receives.

### From Blueprints and Structures

Blueprints, structures, and sets saved on Framework Cloud can be used directly. Log in first with [cloud_login()](ref:cloud_login), then pass a `blueprint` or a `structure` handle.

```python
fw.new("Lab Notes", "~/projects/lab-notes", blueprint="ada/lab")
fw.new(
    "Stats 101",
    "~/teaching/stats-101",
    structure="framework/course",
    tools=["quarto_slides"],
    skill_sets=["ada/teaching"],
    sensitive=False,
)
```

A structure replaces the blueprint's structure. `tools`, `package_sets`, `skill_sets`, and `skills` take handles, where a bare id such as `quarto` means Framework's. `sensitive`, `ai`, and `git` set those settings. Tools that Framework's tools require are added before the choices are sent.

`new()` sends the choices to the server, which resolves them into layers. It builds the project from those layers, then registers it on the server, which records the choices and returns a project token that `new()` saves in `.env`. If registration fails, the project is still created and `new()` reports why. Only the choices you pass are sent. Data and code stay on your computer.

When you are logged in and pass no type, blueprint, structure, tools, or sets, `new()` starts from your defaults on Framework Cloud: your preferred blueprint, tools, sets, and settings. Interactively, it shows your default blueprint and accepts a Framework type or another blueprint instead. When the server can't be reached, `new()` uses the local defaults described above.

## What Gets Created

A new project contains these files, depending on its type.

- The folders of the project type.
- `settings.yml`, plus split files under `settings/` for standard and privacy-sensitive projects.
- `pyproject.toml`, which depends on `framework-py` and tells uv not to build the project as a package.
- `scaffold.py`, which [scaffold()](ref:scaffold) runs after it loads the settings.
- `.env`, with empty placeholders for PostgreSQL and S3 credentials.
- `.gitignore` for the project type. Framework makes sure it ignores `.env`.
- `framework.db`, the project database.
- `_quarto.yml` in the root and in each source folder that renders.
- `AGENTS.md`, the AI assistants' skills, and `CLAUDE.md` when Claude Code is selected.
- A `.code-workspace` file for VS Code and Positron.
- `presentation.qmd` in presentation projects.
- `.framework/spec.json`, the resolved definition the project was built from.

A `bare` project gets only `settings.yml`, `pyproject.toml`, `.gitignore`, and `.framework/spec.json`.

After creation, `new()` does three more things.

1. It initializes a git repository when git is installed and your defaults allow it. It installs the pre-commit hook when a hook check is enabled. It makes the first commit when git's `user.name` and `user.email` are set, and skips the commit with a note otherwise.
2. It records the project in the registry in your global settings, unless the folder is in the system's temporary folder.
3. When you are logged in to Framework Cloud, it registers the project there with its name, type, and language, and saves the project token in `.env`. Bare projects are not registered.

Install the packages before you start work.

```sh
cd ~/projects/vaccine-study
uv sync
```

## Prepare a Cloned Project

A clone lacks the files git does not carry: `.env`, `framework.db`, ignored and empty folders, and the virtual environment. [bootstrap()](ref:bootstrap) restores them. Every step is safe to repeat, and none contacts Framework Cloud.

1. It makes sure `.gitignore` ignores `.env`.
2. It copies `.env.example` to `.env` when `.env` is missing, and lists variables that are still empty.
3. It creates the folders listed in the settings. Entries that name a file, such as `presentation.qmd`, are skipped.
4. It creates `framework.db`, or adds tables a newer version needs.
5. It installs the packages with `uv sync`, using `--frozen` when `uv.lock` exists.
6. It installs the pre-commit hook when a hook check is enabled.

Run it from the command line after cloning.

```sh
git clone https://github.com/example/vaccine-study.git
cd vaccine-study
uv run python -m framework bootstrap
```

The command takes `--path` for another folder, `--interactive` to ask for the values of empty `.env` variables, and `--no-packages` to skip the uv step. Secret-looking variables are asked for without echoing the answer.

From Python, `bootstrap()` returns a `BootstrapResult` with the steps it took and the work left for you.

```python
result = fw.bootstrap("~/projects/vaccine-study", packages=False)
result.todo
```

To share the names of the variables a project needs, commit a `.env.example` file with empty values. `bootstrap()` refuses projects whose `language` is not `python`.

## Building from a Spec

[new()](ref:new) builds a project spec from three layers: the package defaults, your global settings, and the type you chose. Tools that need finer control can use the same steps directly.

- [spec_resolve()](ref:spec_resolve) merges layers into a complete spec and reports keys that more than one layer set.
- [spec_check()](ref:spec_check) lists the problems that would stop creation.
- [project_create()](ref:project_create) builds a project from a resolved spec.

```python
resolved = fw.spec_resolve(
    [{"kind": "project", "layer": {"type": "course", "language": "python"}}],
    name="Stats 101",
)
fw.project_create(resolved.spec, "~/teaching/stats-101")
```

A layer that sets a language other than `python` is refused, because framework-py builds Python projects only.
