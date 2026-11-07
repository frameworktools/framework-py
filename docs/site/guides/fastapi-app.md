---
id: fastapi-app
title: FastAPI app
description: A FastAPI app from Framework Cloud, with app/main.py, tests with a test client, and the fastapi-python package set
section: project_types
position: 12
---

## Overview

The FastAPI app type starts a web API built with [FastAPI](https://fastapi.tiangolo.com/). It is a built-in blueprint on Framework Cloud, `framework/fastapi`. Creating it needs a Framework Cloud sign-in.

## Folders and files

```text
my-api/
├── app/
│   ├── __init__.py
│   └── main.py            # the endpoints
├── data/                  # data the API reads
├── scripts/               # data preparation and other scripts
├── tests/
│   └── test_main.py       # tests with FastAPI's test client
├── conftest.py            # lets the tests import app.main
├── README.md              # how to run the API
├── AGENTS.md
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

The project also gets `.env`, `.gitignore`, and a git repository with a first commit. The `framework-workflow` and `framework-packages` skills are installed for AI assistants.

## Starter files

`app/main.py` creates the app, titled with the project name, and defines two endpoints.

- `GET /health` returns `{"status": "ok"}`.
- `POST /echo` takes a JSON body with a `message` field and returns it. A pydantic model, `Message`, validates the body.

`tests/test_main.py` calls both endpoints through `TestClient`. `README.md` names the project and gives the command that runs the API.

## Package set

The project's `pyproject.toml` lists the `fastapi-python` package set.

| Package | Use |
|---------|-----|
| `fastapi` | The API framework |
| `uvicorn` | The server that runs the app |
| `pydantic` | Request and response models |
| `httpx` | The HTTP client behind `TestClient` |
| `pytest` | Tests |

To add the same packages to an existing project, use [package_set_add()](ref:package_set_add).

```python
import framework as fw

fw.package_set_add("fastapi-python")
```

## Project creation

Sign in to Framework Cloud first. [cloud_login()](ref:cloud_login) opens the approval page in your browser.

```python
import framework as fw

fw.cloud_login()
fw.new_project("My API", "~/projects/my-api", blueprint="framework/fastapi")
```

Pass the type as a blueprint handle. The `type` argument accepts only Framework's built-in types, so `type="fastapi"` raises an error. Signed out, `new_project()` stops and explains how to log in. The command line has no option for this type.

You can also start in Framework Cloud. Open the FastAPI app blueprint on the Blueprints page and choose **New project**. Then create the project on your computer from its project token.

```python
fw.new_project(key="fw_...", location="~/projects/my-api")
```

The [New projects](guide:new-project) guide covers blueprints and project tokens.

## Running the API

Run the API from the project folder.

```sh
uv run uvicorn app.main:app --reload
```

uvicorn serves the API at http://127.0.0.1:8000. The interactive docs are at `/docs`, such as http://127.0.0.1:8000/docs. `--reload` restarts the server when a file changes.

## Tests

Run the tests from the project folder.

```sh
uv run pytest
```

The empty `conftest.py` at the project root puts the project folder on the import path, so `tests/test_main.py` can import `app.main`. Add a test to `tests/` for each new endpoint.
