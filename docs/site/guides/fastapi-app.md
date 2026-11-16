---
id: fastapi-app
title: FastAPI app
description: FastAPI HTTP services from the framework/fastapi blueprint
section: project_types
position: 12
---

## Overview

The `fastapi` project type scaffolds a Python HTTP API built with [FastAPI](https://fastapi.tiangolo.com/) from the built-in `framework/fastapi` blueprint on Framework Cloud. Creating it requires signing in with [cloud_login()](ref:cloud_login).

## Directory layout

```text
my-api/
├── app/
│   ├── __init__.py
│   └── main.py            # FastAPI application and endpoints
├── data/                  # datasets read by the service
├── scripts/               # data preparation scripts
├── tests/
│   └── test_main.py       # TestClient endpoint tests
├── conftest.py            # adds project root to pytest import path
├── README.md              # run instructions
├── AGENTS.md
├── framework.db
├── pyproject.toml
├── scaffold.py
└── settings.yml
```

Project creation also writes `.env`, `.gitignore`, the `framework-workflow` and `framework-packages` AI skills, and an initial git commit.

## Starter endpoints

`app/main.py` initializes a `FastAPI` instance titled with the project name and defines two routes:

- `GET /health`: returns `{"status": "ok"}`.
- `POST /echo`: validates a `{"message": "..."}` request body with a Pydantic `Message` model and echoes it back.

`tests/test_main.py` exercises both endpoints through FastAPI's `TestClient`.

## Included packages

`pyproject.toml` includes the `fastapi-python` package set:

| Package | Role |
|---------|------|
| `fastapi` | HTTP API framework |
| `uvicorn` | ASGI server |
| `pydantic` | Request and response validation |
| `httpx` | HTTP client required by `TestClient` |
| `pytest` | Test runner |

Add the same bundle to an existing project with [package_set_add()](ref:package_set_add):

```python
import framework as fw

fw.package_set_add("fastapi-python")
```

## Project creation

Sign in to Framework Cloud and call [new_project()](ref:new_project) with `type="fastapi"` (or `blueprint="framework/fastapi"`):

```python
import framework as fw

fw.cloud_login()
fw.new_project("My API", "~/code/my-api", type="fastapi")
```

Command-line equivalent:

```sh
python -m framework create --name "My API" --path ~/code/my-api --type fastapi
```

(R's `plumber` type is R-only; passing `type="plumber"` in Python raises an error pointing to `type="fastapi"`.)

You can also open the **FastAPI** blueprint on Framework Cloud, click **Create project**, and scaffold locally from its project token:

```python
fw.new_project(key="fw_...", location="~/code/my-api")
```

See [New projects](guide:new-project) for blueprint and token options.

## Local server and tests

Install dependencies with `uv sync`, then start `uvicorn` with live reload:

```sh
uv run uvicorn app.main:app --reload
```

`uvicorn` serves the API at `http://127.0.0.1:8000` with interactive OpenAPI docs at `http://127.0.0.1:8000/docs`.

Run the test suite with `pytest`:

```sh
uv run pytest
```
