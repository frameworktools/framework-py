---
id: new-project
title: New projects
description: Project creation with fw.new_project() or the command line, and cloned-project setup with bootstrap()
section: getting_started
position: 5
---

## Projects from Python

[new_project()](ref:new_project) creates a Python project from your global defaults. It takes a name, the folder to create, and a project type.

```python
import framework as fw

fw.new_project("Vaccine Study", "~/projects/vaccine-study", type="project")
```

The type is one of `project`, `project_sensitive`, `course`, `presentation`, or `bare`. The [Project types](guide:project-types) guide describes each one.

```python
fw.new_project("Patient Outcomes", "~/projects/patient-outcomes", type="project_sensitive")
fw.new_project("Stats 101", "~/teaching/stats-101", type="course")
fw.new_project("Q4 Review", "~/talks/q4-review", type="presentation")
```

In an interactive session, `new_project()` asks for any value you leave out. It suggests `~/projects/<name>` as the location and `project`, the Standard Project, as the type. In a script or notebook, where standard input is not a terminal, a missing name or location is an error and a missing type means `project`. Pass `type="bare"` for a bare project. Pass `interactive=True` or `interactive=False` to choose the behavior yourself.

The folder must not exist yet. `new_project()` returns the path of the new project.

### Workflows

The workflow decides how notebooks are written. Pass it as `workflow`.

| Workflow | Tools it turns on | `make_notebook()` writes |
|----------|-------------------|--------------------------|
| `jupyter` | `jupyter` and `quarto` | Jupyter notebooks (`.ipynb`), with Quarto kept for rendering and publishing |
| `quarto` | `quarto` | Quarto documents (`.qmd`) |
| `scripts` | none (it turns `quarto` and `jupyter` off) | Python scripts (`.py`) |

Python projects of type `project` and `project_sensitive` use the Jupyter workflow unless you choose another. Course and presentation projects use Quarto, and bare projects have no workflow. A scripts-only project gets no notebook files. Any starter scripts come from its blueprint. The workflow and the notebook format are recorded under `scaffold:` in the project's settings.

```python
fw.new_project("Pipeline", "~/projects/pipeline", type="project", workflow="scripts")
```

### Tools and sets

Tools are optional parts of a project beyond its folders. Each tool adds only its own files, folders, and packages. [tools_available()](ref:tools_available) lists the tools for Python projects.

- `uv` manages the project's dependencies in `pyproject.toml` and `uv.lock`. It is on for every project.
- `quarto` writes the root `_quarto.yml` and render settings, and adds `ipykernel`, `nbformat`, and `nbclient`, which `quarto render` needs to run Python chunks. It is on for every type except `bare`.
- `jupyter` adds the `ipykernel` kernel and `jupyterlab`, so `uv run jupyter lab` opens the notebooks. The Jupyter workflow turns it on.
- `quarto_slides` adds a `slides/` folder that renders as RevealJS.
- `quarto_manuscript` adds a `manuscript/` folder with a Quarto manuscript to start from.

Pass `tools` as a list of tools to turn on, or as a map that can also turn a default off.

```python
fw.new_project(
    "Lab Notes", "~/projects/lab-notes", type="project", tools={"quarto": False, "jupyter": True}
)
fw.new_project("Q4 Review", "~/talks/q4-review", type="project", tools=["quarto_slides"])
```

A tool that another tool needs is turned on too, with a message. `quarto_slides` and `quarto_manuscript` turn on `quarto`. Turning off a tool that another one needs is an error. The project's tools are recorded under `tools` in `settings.yml`. A project without a `tools` section predates it and keeps using Quarto.

`skill_sets` and `package_sets` add bundled sets of AI skills and packages. The `framework` skill set adds Framework's four skills. The tool and set definitions are shared with the R package and Framework Cloud, so a project gets the same pieces whichever one creates it.

With the command line, `--tool ID` turns a tool on and `--tool ID=false` turns it off. `--skill-set` and `--package-set` add sets. Each option can be repeated.

## Projects from the command line

The package's command-line entry point runs `new_project()` with the same defaults.

```sh
python -m framework create --name "Vaccine Study" --path ~/projects/vaccine-study --type project
```

Without `--type`, the project is a Standard Project (`project`). Pass `--type bare` for a bare project. With `--interactive`, the command asks for missing answers. The command prints the project folder on success and exits with status 1 and a message on failure.

When framework-py is not installed in any environment yet, uv can run the command with a temporary copy of the package.

```sh
uv run --with framework-py python -m framework create --name "Vaccine Study" --path ~/projects/vaccine-study --type project
```

### The fw command

The `fw` command-line tool creates projects in every Framework language. For a Python project, it finds a Python runtime with framework-py and runs the command above.

```sh
fw new vaccine-study --lang python --type project
```

`fw` creates the project in `./<name>` unless `--dir` names another folder.

### Layers files

framework.pub describes a project as a layers file. `--from-layers` creates a project from one.

```sh
python -m framework create --from-layers layers.json --path .
```

This form runs headless unless you pass `--interactive`. A headless run asks no questions, initializes no git repository, writes nothing under `.github/workflows/`, and does not register the project. It works in a fresh checkout that holds only `.git`, `.github`, `.framework`, `README.md`, and `LICENSE`, which is how the framework.pub GitHub Action uses it. [project_create_from_layers()](ref:project_create_from_layers) does the same from Python.

## Projects from Framework Cloud

A project defined on Framework Cloud is created from its project token.

```python
fw.new_project(key="fw_...")
fw.new(key="fw_...", url="https://cloud.example.edu")
```

`url` names a self-hosted server that issued the token, so no login is needed first. Without it, the server is `FW_CLOUD_URL`, then the one you last logged in to, then framework.pub. `new_project()` fetches the project's definition from the server and saves the token and server in the new project's `.env` as `FW_PROJECT_TOKEN` and `FW_CLOUD_URL`. The project then publishes to that server and uses its integrity ledger. The [Framework Cloud](guide:cloud) guide describes what the server receives.

### Blueprints and structures

Blueprints, structures, and sets saved on Framework Cloud can be used directly. Log in first with [cloud_login()](ref:cloud_login), then pass a `blueprint` or a `structure` handle.

```python
fw.new_project("Lab Notes", "~/projects/lab-notes", blueprint="ada/lab")
fw.new_project(
    "Stats 101",
    "~/teaching/stats-101",
    structure="framework/course",
    tools=["quarto_slides"],
    skill_sets=["ada/teaching"],
    sensitive=False,
)
```

A structure replaces the blueprint's structure. `tools`, `package_sets`, `skill_sets`, and `skills` take handles, where a bare id such as `quarto` means Framework's. `sensitive`, `ai`, and `git` set those settings. Tools that Framework's tools require are added before the choices are sent.

`new_project()` sends the choices to the server, which resolves them into layers. It builds the project from those layers, then registers it on the server, which records the choices and returns a project token that `new_project()` saves in `.env`. If registration fails, the project is still created and `new_project()` reports why. Only the choices you pass are sent. Data and code stay on your computer.

When you are logged in, Framework Cloud resolves every choice, as it does for R and `fw`. A type is sent as Framework's blueprint of that name, such as `framework/project`, together with any workflow, tools, and sets. With no choices at all, the cloud resolves your defaults. `new_project()` saves that result in Framework's config folder, under `cache/defaults/`, one file per server and language, in the format R and `fw` share.

When the server cannot be reached, `new_project()` builds your defaults from that cache and says so: "Using your Framework Cloud defaults from 2026-11-01". Otherwise, including any explicit choice and a server too old to resolve projects, an interactive session asks before building from the built-in defaults described above. A script stops with an error unless you pass `offline=True`. `new_project()` never switches to a different configuration silently. A rejected token is reported as an error, never treated as offline. When you are not logged in, `new_project()` uses the built-in defaults without asking.

```python
fw.new_project("Field Notes", "~/projects/field-notes", offline=True)
```

## Project contents

A new project contains these files, depending on its type.

- The folders of the project type.
- `settings.yml`, plus split files under `settings/` for standard and privacy-sensitive projects.
- `pyproject.toml`, which depends on `framework-py` and the tools its workflow needs (see Tools above) and tells uv not to build the project as a package.
- `scaffold.py`, which [scaffold()](ref:scaffold) runs after it loads the settings.
- `.env`, with empty placeholders for PostgreSQL and S3 credentials.
- `.gitignore` for the project type. Framework makes sure it ignores `.env`.
- `framework.db`, the project database.
- `_quarto.yml` in the root, and `_metadata.yml` in a folder that renders in another format, such as a course's `slides/`.
- `AGENTS.md`, the AI assistants' skills, and `CLAUDE.md` when Claude Code is selected.
- A `.code-workspace` file for VS Code and Positron.
- `presentation.qmd` in presentation projects.
- `.framework/spec.json`, the resolved definition the project was built from.

A `bare` project gets only `settings.yml`, `pyproject.toml`, `.gitignore`, and `.framework/spec.json`.

After creation, `new_project()` does three more things.

1. It initializes a git repository when git is installed and your defaults allow it. It makes the first commit when git's `user.name` and `user.email` are set, and skips the commit with a note otherwise. Then it installs the pre-commit hook when a hook check is enabled, as it is in a privacy-sensitive project.
2. It records the project in the registry in your global settings, unless the folder is in the system's temporary folder.
3. When you are logged in to Framework Cloud, it registers the project there with its name, type, and language, and saves the project token in `.env`. Bare projects are not registered.

Install the packages before you start work.

```sh
cd ~/projects/vaccine-study
uv sync
```

## Adopting an existing project

[adopt()](ref:adopt) turns a folder you already have into a Framework project, in place. It lists the folder's subfolders, at most three levels deep, and proposes a role for each: a Framework directory key such as `inputs_raw`, a folder of its own under `extra_directories`, or a folder of rendered notebooks under `render_dirs`. It reads folder names only, never files or their contents. Hidden folders, version control, environments such as `.venv` and `renv`, caches, Framework's `settings/` folder, and Quarto's `*_files/` and `*_cache/` folders are left out.

```python
fw.adopt("~/projects/old-study")  # review the mapping
fw.adopt("~/projects/old-study", yes=True)  # accept it as proposed
fw.adopt("~/projects/cohort", yes=True, sensitive=True)  # the project holds sensitive data
fw.adopt("~/teaching/methods", yes=True, type="course")  # choose the type
fw.adopt("~/projects/old-study", yes=True, cloud=True)  # register and link it too
```

The type follows from the folders unless you pass `type`: `course` when the folder has at least two course folders (`slides/`, `modules/`, `assignments/`, `readings/`, and similar), `project_sensitive` when the private and public layout is present (`inputs/private/raw/`, `outputs/public/figures/`), `project` when any folder maps to a Framework directory, and `bare` otherwise. A folder with no subfolders, such as one `analysis.qmd`, is adopted with no folders.

You review the proposal before anything is written. Press Enter to accept it, type a folder's number to change its role, or type `-` and the number to leave it out. With `yes=True` the mapping is printed and accepted. Then `adopt()` writes `settings.yml` and `.framework/spec.json`, and appends the rules of the type's `.gitignore` template that the folder's `.gitignore` lacks, under `# Added by framework.adopt()`. It never removes a `.gitignore` line, and a rule that would ignore a file git tracks is left out and reported. The spec records the folder as it is: the mapped folders only, no tools, no default packages, AI files off, and git on when the folder is a git repository. Nothing is moved.

A folder whose path contains "private" gets a warning: "'data/private' looks private. Keep it out of git and away from AI assistants." The review then asks whether the project holds sensitive data. With `yes=True` the setting stays off unless you pass `sensitive=True`. framework-py adopts Python projects: a folder whose files say R or Stata (an `.Rproj`, `renv.lock`, `.R` files) is refused unless you pass `force=True`. A folder that is already a Framework project is refused too; use `upgrade()`. A `config.yml` counts only when it has Framework keys, so a tool's own `config.yml` doesn't block adoption.

From a shell:

```bash
python -m framework adopt --path ~/projects/old-study --yes [--type TYPE] [--sensitive] [--cloud [--slug SLUG]]
```

The R package and `fw adopt` map folders with the same shared rules. When you are logged in and decline to register, `adopt()` offers to save the folder layout as a structure. [structure_import()](ref:structure_import) does that on its own. It sends only the folder paths, and the structure is private to you.

```python
fw.structure_import("~/projects/old-study", name="Lab layout")
```

## Upgrading an older project

[upgrade()](ref:upgrade) brings a project made by an older framework-py (or a 1.x `config.yml` project) to the current layout. It prints a plan and changes nothing until you apply it:

```python
fw.upgrade("~/projects/old-study")  # print the plan
fw.upgrade("~/projects/old-study", dry_run=False)  # apply it
```

```bash
python -m framework upgrade            # the plan, as fw project upgrade shows it
python -m framework upgrade --apply    # apply it
```

The plan adds missing `tools:` and `security:` sections to the settings (a `project_sensitive` project gets `sensitive: true`), rewrites old connection layouts, appends missing `.gitignore` rules, makes AGENTS.md the canonical AI context file with CLAUDE.md a pointer to it, updates Framework skills that match a released version (in the assistants' skills folders and in `.agents/skills`), adds missing Quarto keys without replacing your configuration, and writes `.framework/spec.json`. An existing spec is refreshed when the settings' folders, type, author, tools, security, or connections, or the `.gitignore` it copies, have changed. The spec holds only what spec.v1 allows, the schema Framework Cloud checks, so keys an earlier `upgrade()` wrote that Cloud refuses (`scaffold.positron`, `source.upgraded_from`) are dropped. Keys outside `default:`, which Framework ignores, are reported; `move_settings=True` (`--move-settings`) moves each into `settings/<key>.yml` as written, comments included, and names it under `default:`, so `settings_read()` reads it. A password written into the settings is reported, not changed: the plan names the variable the spec refers to it by (the one `.env`, an example `.env`, or your code already uses, else one made from the connection's name) and tells you to set it in `.env`. Every file it rewrites is first copied to `.framework/backups/<timestamp>/`, which git ignores (`--no-backup` or `backup=False` skips the copy). Running it again changes nothing.

`type=` changes the project's type in the same pass (`--type` on the command line). Making a project sensitive is one step:

```python
fw.upgrade("~/projects/old-study", type="project_sensitive", dry_run=False)
```

It sets `security: sensitive: true` and adds the private and public folders of the sensitive layout that the project lacks; folders it has keep their keys and paths, so code that names them keeps working. The sensitive `.gitignore` rules, the Claude Code guard, and the `framework-sensitive-data` skill follow. From then on `save_table()`, `save_figure()`, `save_model()`, and `save_report()` write to private folders (`outputs/private/tables` and so on) unless you pass `public=True`; the plan lists those moves, and the data and render folders that aren't private. A sensitive project given another type stays sensitive.

## Registering on Framework Cloud

[cloud_register()](ref:cloud_register) creates the Framework Cloud project for a project that exists only on your computer, from its `.framework/spec.json`, and links the folder: the project token and server go to `.env`, which git is made to ignore first. The Cloud project gets exactly this folder's type, folders, and tools, not your defaults, and an adopted project is marked as adopted. It is what `fw project link --create` does.

```python
fw.cloud_register("~/projects/old-study", dry_run=True)  # show the request
fw.cloud_register("~/projects/old-study", slug="old-study")
```

A project with no spec needs `upgrade()` first. A project already linked is left alone. When you already have a project with the slug, an interactive session offers to link the folder to it instead (as `fw project link --from SLUG` does); a slug held by a project in your Trash can't be reused.

## Preparing a cloned project

A clone lacks the files git does not carry: `.env`, `framework.db`, ignored and empty folders, and the virtual environment. [bootstrap()](ref:bootstrap) restores them. Every step is safe to repeat, and none contacts Framework Cloud.

1. It makes sure `.gitignore` ignores `.env`.
2. It copies `.env.example` to `.env` when `.env` is missing, and lists variables that are still empty and that the settings read with `env()`.
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

## Building from a spec

[new_project()](ref:new_project) builds a project spec from three layers: the package defaults, your global settings, and the type you chose. Tools that need finer control can use the same steps directly.

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

`new_project()` is the canonical project creation function. `new()` remains a compatible alias.
