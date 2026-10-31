---
id: cloud
title: Framework Cloud
description: Logging in, consent, token storage, settings sync, the integrity ledger, and what the cloud receives
section: features
position: 11
---

## Overview

Framework Cloud is an optional service at framework.pub, which organizations can also host themselves. It syncs your settings across machines, defines projects that collaborators create from a token, keeps an integrity ledger of data file hashes, and hosts published documents.

Every cloud feature is opt-in. framework-py contacts a server only after you log in, or in a project that was set up from the cloud. Data files and code are never uploaded, except documents you publish yourself.

## What the Cloud Receives

Connecting a machine enables these transfers when you use the related features.

| Feature | What is sent | Sent by |
|---------|--------------|---------|
| Settings sync | Your Framework settings document | [cloud_sync()](ref:cloud_sync) |
| Project registration | The project's name, language, structure, tools, chosen sets, and its sensitive-data, git, and AI settings | [new()](ref:new), when you are logged in |
| Integrity ledger | Catalog names, file hashes, file sizes, and the machine's hostname | [data_save()](ref:data_save) in live mode, and [ledger_push()](ref:ledger_push) |
| Publishing | The documents you publish | [publish()](ref:publish) and [publish_notebook()](ref:publish_notebook) |

The cloud never receives the contents of data files, the project's code, its settings files, its `.env` file, or `framework.db`. The server's privacy page, at `/privacy`, describes how the server handles what it receives.

## Logging In

[cloud_login()](ref:cloud_login) connects this machine to a server with a personal token from the server's token page.

```python
import framework as fw

fw.cloud_login("fw_...")
fw.cloud_login("fw_...", url="https://framework.example.edu")
```

Before it connects, the function prints a consent notice that lists the transfers above and asks you to confirm. In a non-interactive session, such as a script or CI job, it raises an error unless you pass `consent=True` after reviewing the notice.

```python
fw.cloud_login(token, consent=True)
```

With `verify=True`, the default, the token is checked with the server before it is saved. The server becomes your default, recorded in `cloud.yml` in the config folder.

### Where the Token Is Stored

The token is stored in the system keyring under the service `framework-cloud:<host>` with the username `token`. The R package and the `fw` tool read and write the same entry, so logging in once serves all three. On Linux and Windows, framework-py writes the entry in the exact format R uses.

When no keyring is available, the token is saved to a file in the config folder that only you can read: `cloud-token` for framework.pub, and `cloud-token-<host>` for other servers. When the `FW_TOKEN_FILE` environment variable is set, the token is written to that file instead.

A token is looked up in this order.

1. The `FW_CLOUD_TOKEN` environment variable.
2. The file named by `FW_TOKEN_FILE`, which is authoritative when set.
3. The system keyring.
4. The token file in Python's config folder, then the one in R's.

### Status and Logout

[cloud_status()](ref:cloud_status) prints the server in use, where that setting came from, and the account the token belongs to. It returns `True` when the server accepts the token.

```python
fw.cloud_status()
```

[cloud_logout()](ref:cloud_logout) removes the stored token for a server from the keyring and the config folder, and returns what it removed.

```python
fw.cloud_logout()
```

## Choosing the Server

Calls made on your behalf, such as logging in and syncing settings, use the first of these that is set.

1. The `url` argument.
2. The `FW_CLOUD_URL` environment variable.
3. The server you last logged in to.
4. `https://framework.pub`.

Calls made for a project, such as publishing and the ledger, use the `url` argument, then `FW_CLOUD_URL` in the project's `.env`, then the environment variable, then `https://framework.pub`. A project token is valid only on the server that issued it, so project calls never fall back to your login default.

Server addresses must use `https`. Plain `http` is accepted only for `localhost`, `127.0.0.1`, and `[::1]`.

## Projects from the Cloud

A project defined on the cloud is created from its project token.

```python
fw.new(key="fw_...")
```

The new project's `.env` receives `FW_PROJECT_TOKEN` and `FW_CLOUD_URL`. The token stays in `.env` and never goes into the settings. When you are logged in, `new()` also registers each project it creates, except bare projects, and saves the new project's token in its `.env`.

## Settings Sync

The cloud keeps one settings document per account. [cloud_settings()](ref:cloud_settings) returns it. [cloud_sync()](ref:cloud_sync) keeps a local copy in step with it.

```python
fw.cloud_sync()
fw.cloud_sync("pull")
fw.cloud_sync("push")
```

The local copy is `cloud-settings.yml` in the config folder, and `sync-state.yml` records the revision of the last sync.

- `"pull"` takes the cloud version. Local edits are first backed up to a timestamped copy.
- `"push"` sends the local copy. The server rejects a push when the cloud changed since the last sync.
- `"auto"`, the default, pulls when there is no local copy yet, pushes when only the local copy changed, and pulls when only the cloud changed. When both changed, it stops and asks you to choose.

[new()](ref:new) reads your defaults from the global `settings.yml`. The synced copy is a separate file.

## The Integrity Ledger

Every project records its data file hashes in `framework.db`. The ledger keeps a second record of those hashes on the cloud, so collaborators on different machines can confirm that they hold the same files. The ledger needs a project token.

### Live Mode

In live mode, every [data_save()](ref:data_save) sends the file's catalog name, SHA-256 hash, size, save time, and this machine's hostname to the ledger. Turn it on with the `FW_LEDGER` environment variable, set to `live`, `on`, or `true`, or in the settings.

```yaml
options:
  cloud_ledger: live
```

`FW_LEDGER=off` turns live mode off even when the settings turn it on. A missing project token or an unreachable server never fails a save. The save completes, and `framework.db` remains the record.

### Pushing and Listing

[ledger_push()](ref:ledger_push) sends the hashes in `framework.db` that differ from the newest ledger entry for each name, and returns the counts pushed and already current. [ledger_list()](ref:ledger_list) returns the ledger, newest first.

```python
fw.ledger_push()
fw.ledger_list()
fw.ledger_list("inputs.raw.survey")
```

### Verifying a File

[data_verify()](ref:data_verify) hashes a catalog file and compares the hash with its `framework.db` record and, when the project has a token, with the newest ledger entry. It returns `True` when every available record matches, and issues a warning otherwise.

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

## Using Both R and Python

When Python's config folder has no saved server or token file, framework-py reads the ones in R's config folder. [config_sync()](ref:config_sync) copies `cloud.yml` and the token files into Python's folder. Keyring entries need no copying, because both languages use the same entry.
