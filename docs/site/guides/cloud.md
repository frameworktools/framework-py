---
id: cloud
title: Framework Cloud
description: Sign-in, token storage, server selection, settings sync, and the integrity ledger
section: features
position: 11
---

## Overview

Framework Cloud (`framework.pub` or a self-hosted server) syncs personal defaults across machines, provisions team projects from tokens and blueprints, maintains an append-only SHA-256 integrity ledger, and hosts published artifacts (1 GB free on `framework.pub`).

All Cloud features are opt-in. `framework-py` contacts a server only after you sign in or link a project token, and never uploads raw data files, source code, `settings.yml`, `.env`, or `framework.db`.

## Transmitted metadata

| Feature | Transmitted fields | Trigger |
|---------|--------------------|---------|
| Settings sync | Global Framework settings document | [cloud_sync()](ref:cloud_sync) |
| Project registration | Project name, language, structure, tools, package/skill sets, and `sensitive`/`git`/`ai` flags | [new_project()](ref:new_project) when signed in, or [cloud_register()](ref:cloud_register) |
| Integrity ledger | Catalog keys, SHA-256 digests, hostname, and (in live mode) byte sizes | [data_save()](ref:data_save) in live mode, [ledger_push()](ref:ledger_push) |
| Publishing | Explicitly published artifacts or publication metadata | [publish()](ref:publish), [publish_notebook()](ref:publish_notebook) |

See `/privacy` on your Framework Cloud server for data retention details.

## Sign-in

[cloud_login()](ref:cloud_login) links your machine to Framework Cloud. Called without a token, it opens the browser approval page (`browser=False` prints the URL instead):

```python
import framework as fw

fw.cloud_login()
fw.cloud_login(url="https://framework.example.edu", browser=False)
```

You can also pass a personal API token from **Account > Computers & API tokens**:

```python
fw.cloud_login("fw_...")
fw.cloud_login("fw_...", url="https://framework.example.edu")
```

With a pasted token, interactive sessions display the data-transfer notice and prompt for confirmation; non-interactive scripts and CI jobs require `consent=True`:

```python
fw.cloud_login(token, consent=True)
```

By default (`verify=True`), `cloud_login()` verifies the token with the server and records the server URL in `cloud.yml`.

### Token storage and lookup

`framework-py`, the R package, and `fw` share the system keyring entry under service `framework-cloud:<host>` (`username="token"`). On macOS, `framework-py` accesses Keychain via `/usr/bin/security` (matching `fw`) to avoid repeated authorization prompts. When no keyring is available, tokens write to owner-only files (`cloud-token` or `cloud-token-<host>`) in the configuration directory, or to `FW_TOKEN_FILE` when set.

Token lookup order:

1. `FW_CLOUD_TOKEN` environment variable.
2. `FW_TOKEN_FILE` (authoritative when set).
3. System keyring.
4. Token file in Python's configuration directory, then R's configuration directory.

### Status and sign-out

[cloud_status()](ref:cloud_status) prints the active server URL, where that setting came from, and the account identity, returning `True` when authenticated:

```python
fw.cloud_status()
```

[cloud_logout()](ref:cloud_logout) revokes the stored token on the server and deletes it from the keyring, configuration directory, and `FW_TOKEN_FILE` (tokens passed via `FW_CLOUD_TOKEN` remain active until unset):

```python
fw.cloud_logout()
```

## Server resolution

Account-level calls (`cloud_status()`, `cloud_sync()`, `new_project()`) check:

1. Explicit `url` argument.
2. `FW_CLOUD_URL` environment variable.
3. Saved server in `cloud.yml`, from the last `cloud_login()`, `fw login`, or R login.

Project-level calls (`publish()`, `ledger_push()`, `data_verify()`) check:

1. Explicit `url` argument.
2. Project `.env` `FW_CLOUD_URL`.
3. Process `FW_CLOUD_URL`.
4. Saved server in `cloud.yml`. When saved logins name different servers, the call raises `AmbiguousServerError` and asks for `FW_CLOUD_URL` in `.env`.

A token is valid only on the server that issued it. A project whose `.env` has `FW_PROJECT_TOKEN` but no `FW_CLOUD_URL` skips step 4: without a `url` argument or a process `FW_CLOUD_URL`, project-level calls raise `ProjectServerMissingError`. Add `FW_CLOUD_URL=<server>` to `.env` to fix it.

With none set, calls raise `NoServerError`. Framework never falls back to `https://framework.pub`. `cloud_login()` without `url`, `FW_CLOUD_URL`, or a saved server names `https://framework.pub` in its notice before connecting. Non-loopback URLs require `https://`.

## Cloud project tokens

Create a local checkout from a project token without signing in first:

```python
fw.new_project(key="fw_...")
fw.new(key="fw_...", url="https://cloud.example.edu")
```

`new_project()` writes `FW_PROJECT_TOKEN` and `FW_CLOUD_URL` into the project's `.env`. Every call that writes a new project token also writes the server that issued it, replacing an empty or different `FW_CLOUD_URL`. A project that already has a token keeps its `.env` unchanged. When signed in, `new_project()` also registers newly created non-`bare` projects automatically.

## Account settings sync

[cloud_settings()](ref:cloud_settings) fetches your account settings document, and [cloud_sync()](ref:cloud_sync) synchronizes the local mirror (`cloud-settings.yml`, tracked by `sync-state.yml`):

```python
fw.cloud_sync()
fw.cloud_sync("pull")
fw.cloud_sync("push")
```

- `"auto"` (default): pulls when no local mirror exists or only Cloud changed, pushes when only local changed, and stops on concurrent edits.
- `"pull"`: backs up local edits to a timestamped file and writes the Cloud revision.
- `"push"`: uploads local edits (rejected if Cloud advanced since last sync).

## Integrity ledger

The integrity ledger mirrors `framework.db` dataset hashes on Framework Cloud so collaborators can verify identical inputs across machines using `FW_PROJECT_TOKEN`.

### Live mode

Enable live mode via `FW_LEDGER=live` (`on` or `true`) or in `settings.yml` so every [data_save()](ref:data_save) records the catalog key, SHA-256 digest, byte size, timestamp, and hostname on the Cloud ledger:

```yaml
options:
  cloud_ledger: live
```

Setting `FW_LEDGER=off` overrides `settings.yml`. Missing tokens or network errors never abort a local `data_save()`.

### Manual push, listing, and verification

[ledger_push()](ref:ledger_push) uploads local `framework.db` hashes that differ from the latest Cloud ledger entry, and [ledger_list()](ref:ledger_list) queries ledger history newest first:

```python
fw.ledger_push()
fw.ledger_list()
fw.ledger_list("inputs.raw.survey")
```

[data_verify()](ref:data_verify) hashes a dataset on disk and compares it against both `framework.db` and the latest Cloud ledger entry, returning `True` when all records match:

```python
fw.data_verify("inputs.raw.survey")
```

```text
Verifying inputs.raw.survey (.../inputs/raw/survey.csv)
  file sha256: 71da0ff755e5d5c3...
  [ok] matches local framework.db record
  [ok] matches cloud ledger entry #12 (recorded 2026-10-30T14:02:11Z)
[ok] Verified
```

## R configuration sharing

Keyring tokens work across R, Python, and `fw` immediately. When Python's configuration directory has no `cloud.yml` or fallback token file, `framework-py` reads R's configuration directory, or you can copy them explicitly with [config_sync()](ref:config_sync).
