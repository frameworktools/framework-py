---
id: shiny-app
title: Shiny for Python
description: A Shiny for Python app from Framework Cloud, with app.py, www/, tests/, and the shiny-python package set
section: project_types
position: 11
---

## Overview

The Shiny for Python type starts an interactive web app built with [Shiny for Python](https://shiny.posit.co/py/). It is a built-in blueprint on Framework Cloud, `framework/shiny_python`. Creating it needs a Framework Cloud sign-in.

## Folders and files

```text
my-app/
├── data/                  # data the app reads
├── scripts/               # data preparation and other scripts
├── tests/                 # app tests
├── www/                   # static files, such as images and CSS
├── app.py                 # the app's interface and server
├── README.md              # how to run the app
├── AGENTS.md
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

The project also gets `.env`, `.gitignore`, and a git repository with a first commit. The `framework-workflow` and `framework-packages` skills are installed for AI assistants.

## Starter files

`app.py` is a working app. It uses `ui.page_sidebar()` with a slider in the sidebar and a card that shows a data frame. The slider sets how many rows of random values the table shows. The page title is the project name.

`README.md` names the project and gives the command that runs the app.

## Package set

The project's `pyproject.toml` lists the `shiny-python` package set.

| Package | Use |
|---------|-----|
| `shiny` | The app framework |
| `shinywidgets` | Interactive widgets in Shiny |
| `plotly` | Interactive charts |
| `pandas` | Data frames |

To add the same packages to an existing project, use [package_set_add()](ref:package_set_add).

```python
import framework as fw

fw.package_set_add("shiny-python")
```

## Project creation

Sign in to Framework Cloud first. [cloud_login()](ref:cloud_login) opens the approval page in your browser.

```python
import framework as fw

fw.cloud_login()
fw.new_project("My App", "~/projects/my-app", type="shiny")
```

In Python, `type="shiny"` is Shiny for Python. `type="shiny_python"` is the same. Both equal `blueprint="framework/shiny_python"`. Signed out, `new_project()` stops and says to run `cloud_login()`.

```python
fw.new_project("My App", "~/projects/my-app", blueprint="framework/shiny_python")
```

On the command line, pass `--type shiny`.

```sh
python -m framework create --name "My App" --path ~/projects/my-app --type shiny
```

You can also start in Framework Cloud. Open the Shiny for Python blueprint on the Blueprints page and choose **New project**. Then create the project on your computer from its project token.

```python
fw.new_project(key="fw_...", location="~/projects/my-app")
```

The [New projects](guide:new-project) guide covers blueprints and project tokens.

## Running the app

Run the app from the project folder.

```sh
uv run shiny run --reload app.py
```

Shiny prints the local address, usually http://127.0.0.1:8000. `--reload` restarts the app when a file changes. Without uv, activate the project's environment and run `shiny run --reload app.py`.
