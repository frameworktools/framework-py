---
id: shiny-app
title: Shiny for Python
description: Shiny for Python web applications from the framework/shiny_python blueprint
section: project_types
position: 11
---

## Overview

The `shiny` (`shiny_python`) project type scaffolds an interactive [Shiny for Python](https://shiny.posit.co/py/) web application from the built-in `framework/shiny_python` blueprint on Framework Cloud. Creating it requires signing in with [cloud_login()](ref:cloud_login).

## Directory layout

```text
my-app/
├── data/                  # application datasets
├── scripts/               # data preparation scripts
├── tests/                 # application tests
├── www/                   # static assets (CSS, images)
├── app.py                 # Shiny UI and server definition
├── README.md              # run instructions
├── AGENTS.md
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

Project creation also writes `.env`, `.gitignore`, the `framework-workflow` and `framework-packages` AI skills, and an initial git commit.

## Starter application

`app.py` defines a runnable `ui.page_sidebar()` app titled with the project name, pairing a row-count slider in the sidebar with a rendered DataFrame card.

## Included packages

`pyproject.toml` includes the `shiny-python` package set:

| Package | Role |
|---------|------|
| `shiny` | Web application framework |
| `shinywidgets` | Jupyter/HTML widget integration |
| `plotly` | Interactive charts |
| `pandas` | Tabular data manipulation |

Add the same bundle to an existing project with [package_set_add()](ref:package_set_add):

```python
import framework as fw

fw.package_set_add("shiny-python")
```

## Project creation

Sign in to Framework Cloud and call [new_project()](ref:new_project) with `type="shiny"` (or `type="shiny_python"` / `blueprint="framework/shiny_python"`):

```python
import framework as fw

fw.cloud_login()
fw.new_project("My App", "~/code/my-app", type="shiny")
```

Command-line equivalent:

```sh
python -m framework create --name "My App" --path ~/code/my-app --type shiny
```

You can also open the **Shiny for Python** blueprint on Framework Cloud, click **Create project**, and scaffold locally from its project token:

```python
fw.new_project(key="fw_...", location="~/code/my-app")
```

See [New projects](guide:new-project) for blueprint and token options.

## Local server

Install dependencies with `uv sync`, then start the live-reloading development server from the project root:

```sh
uv run shiny run --reload app.py
```

Shiny serves the app locally (typically `http://127.0.0.1:8000`) and reloads automatically on file changes.
