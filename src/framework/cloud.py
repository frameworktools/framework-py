"""Framework Cloud (framework.pub, or a self-hosted server). Everything here is opt-in.

Nothing contacts a server unless you log in or the project was set up from
the cloud. Data files and code are never uploaded: the cloud receives your
settings document, project names, file digests for the integrity ledger,
and documents you publish.

The server is resolved per call, as in R:

- User calls (login, settings, sync, ``new(key=...)``): the ``url``
  argument, then ``FW_CLOUD_URL``, then the server saved by
  ``cloud_login()``, then https://framework.pub.
- Project calls (publishing, the ledger): the ``url`` argument, then
  ``FW_CLOUD_URL`` in the project's ``.env``, then the environment variable,
  then https://framework.pub. A project token is only valid on the server
  that issued it, so these calls never use the login default.

Plain ``http`` is accepted only for localhost.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
import webbrowser
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from framework import _keychain, _secretservice, _wincred, _yaml, db, dotenv
from framework._log import inform, show_messages
from framework._paths import rendered_page
from framework._private import confirm_private_upload
from framework.config_home import config_dir, ensure_config_dir, r_config_dir
from framework.errors import FrameworkError, FrameworkWarning
from framework.integrity import file_hash
from framework.project import find_settings_file

DEFAULT_URL = "https://framework.pub"
LEGACY_SERVICE = "framework.pub"
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
_TIMEOUT = 30.0


class CloudError(FrameworkError):
    """A Framework Cloud request failed."""


def _version() -> str:
    from framework import __version__

    return __version__


# Servers --------------------------------------------------------------------------


def _authority(url: str) -> str:
    authority = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url)
    authority = re.sub(r"[/?#].*$", "", authority)
    return re.sub(r"^.*@", "", authority).lower()


def hostname(url: str) -> str:
    """The host without scheme, port, path, or user info."""
    authority = _authority(url)
    if authority.startswith("["):
        return authority.split("]")[0] + "]"
    return authority.split(":")[0]


def host(url: str) -> str:
    """Host and port as people read them (``localhost:8080``). Keys the stored token."""
    return _authority(url)


def normalize_url(url: str) -> str:
    """Tidy a server URL and refuse plain http except for localhost."""
    url = url.strip().rstrip("/")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = f"https://{url}"
    scheme = url.split("://", 1)[0].lower()
    name = hostname(url)
    if not name:
        raise CloudError(f"Not a server URL: {url}")
    if scheme == "http":
        if name not in _LOCAL_HOSTS:
            raise CloudError(
                f"Refusing plain http for {name}. Use https, or http only for localhost "
                "and 127.0.0.1 (single-user mode)."
            )
    elif scheme != "https":
        raise CloudError(f"Server URLs must start with https:// (got {scheme}://).")
    return url


def is_default(url: str) -> bool:
    return host(url) == host(DEFAULT_URL)


def _saved_url() -> str | None:
    """The default server a login saved, from the newer of fw's and R's cloud.yml.

    The most recently written file wins, so the last login in any tool counts.
    On a tie fw's folder comes first. fw and R use the same rule.
    """
    best: str | None = None
    best_time = -1
    for folder in dict.fromkeys((config_dir(), r_config_dir())):
        path = folder / "cloud.yml"
        if not path.is_file():
            continue
        data = _yaml.read(path)
        server = data.get("server") if isinstance(data, dict) else None
        if isinstance(server, str) and server:
            written = path.stat().st_mtime_ns
            if written > best_time:
                best, best_time = server, written
    return best


def _project_dir(project_dir: str | Path | None) -> Path | None:
    if project_dir is not None:
        return Path(project_dir)
    found = find_settings_file()
    return found.parent if found else None


def _project_env(key: str, project_dir: str | Path | None = None) -> str | None:
    root = _project_dir(project_dir)
    return dotenv.value(root / ".env", key) if root else None


@dataclass(frozen=True)
class Server:
    url: str
    source: str


def cloud_server(
    url: str | None = None, scope: str = "user", project_dir: str | Path | None = None
) -> Server:
    """Which server a call uses, and where that setting came from."""
    candidates: list[tuple[str | None, str]] = [(url, "the url argument")]
    if scope == "project":
        candidates.append((_project_env("FW_CLOUD_URL", project_dir), "the project's .env"))
    candidates.append((os.environ.get("FW_CLOUD_URL"), "the FW_CLOUD_URL environment variable"))
    if scope == "user":
        candidates.append((_saved_url(), "your cloud_login() default"))
    candidates.append((DEFAULT_URL, "the built-in default"))
    value, source = next((v, s) for v, s in candidates if v)
    return Server(normalize_url(str(value)), source)


# Tokens ---------------------------------------------------------------------------


def keyring_service(url: str) -> str:
    return f"framework-cloud:{host(url)}"


def token_file_name(url: str) -> str:
    if is_default(url):
        return "cloud-token"
    return "cloud-token-" + re.sub(r"[^a-z0-9.-]", "_", host(url))


def _legacy_token_file() -> Path:
    return Path.home() / ".secrets" / "framework" / "token"


def _read_token_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    first = path.read_text(encoding="utf-8").splitlines()
    token = first[0].strip() if first else ""
    return token or None


def _write_token_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{token}\n", encoding="utf-8")
    path.chmod(0o600)


# Windows and Linux entries are read and written in R's format, so R, fw,
# and Python share one entry per server. On macOS, /usr/bin/security reads and
# writes them, as fw does, so the Keychain never asks for access.


def _keyring_get(service: str) -> str | None:
    if sys.platform == "darwin":
        return _keychain.get(service, "token")
    if sys.platform == "win32":
        return _wincred.get(service, "token")
    if sys.platform == "linux":
        try:
            return _secretservice.get(service, "token")
        except Exception:
            pass
    try:
        import keyring

        value = keyring.get_password(service, "token")
    except Exception:
        return None
    return str(value) if value else None


def _keyring_set(service: str, token: str) -> bool:
    if sys.platform == "darwin":
        return _keychain.set(service, "token", token)
    try:
        if sys.platform == "win32":
            _wincred.set(service, "token", token)
        elif sys.platform == "linux":
            _secretservice.set(service, "token", token)
        else:
            import keyring

            keyring.set_password(service, "token", token)
    except Exception:
        return False
    return True


def _keyring_delete(service: str) -> bool:
    if sys.platform == "darwin":
        return _keychain.delete(service, "token")
    try:
        if sys.platform == "win32":
            return _wincred.delete(service, "token")
        if sys.platform == "linux":
            return _secretservice.delete(service, "token")
        import keyring

        keyring.delete_password(service, "token")
    except Exception:
        return False
    return True


def cloud_token(url: str) -> str | None:
    """The stored token for a server.

    ``FW_CLOUD_TOKEN``, then ``FW_TOKEN_FILE`` (authoritative when set), then
    the system keyring, then the token file in Python's config folder, then
    the one in R's. For framework.pub, the older keyring service and
    ``~/.secrets/framework/token`` are read last.
    """
    if os.environ.get("FW_CLOUD_TOKEN"):
        return os.environ["FW_CLOUD_TOKEN"]
    if os.environ.get("FW_TOKEN_FILE"):
        return _read_token_file(Path(os.environ["FW_TOKEN_FILE"]).expanduser())
    token = _keyring_get(keyring_service(url))
    if token is None and is_default(url):
        token = _keyring_get(LEGACY_SERVICE)
    for folder in (config_dir(), r_config_dir()):
        token = token or _read_token_file(folder / token_file_name(url))
    if token is None and is_default(url):
        token = _read_token_file(_legacy_token_file())
    return token


def _require_token(url: str) -> str:
    token = cloud_token(url)
    if token is None:
        login = (
            'fw.cloud_login("fw_...")'
            if is_default(url)
            else f'fw.cloud_login("fw_...", url="{url}")'
        )
        raise CloudError(
            f"No cloud token found for {host(url)}. Create one at {url}/tokens and run:\n  {login}"
        )
    return token


def project_token(project_dir: str | Path | None = None) -> str | None:
    """The project's cloud key: ``FW_PROJECT_TOKEN``, then the project's ``.env``."""
    return os.environ.get("FW_PROJECT_TOKEN") or _project_env("FW_PROJECT_TOKEN", project_dir)


def _require_project_token() -> str:
    token = project_token()
    if token is None:
        raise CloudError(
            "No project token found (FW_PROJECT_TOKEN / .env). "
            "Create the project with framework.new() while logged in."
        )
    return token


# Requests ---------------------------------------------------------------------------


# Tests replace the transport to answer requests without a network.
_transport: httpx.BaseTransport | None = None


def _client() -> httpx.Client:
    return httpx.Client(
        transport=_transport,
        timeout=_TIMEOUT,
        verify=not os.environ.get("FW_CLOUD_INSECURE"),
        headers={"Accept": "application/json", "User-Agent": f"framework-py/{_version()}"},
    )


class CloudOfflineError(CloudError):
    """The server couldn't be reached: a network error, a timeout, or a 5xx reply.

    401 and 403 are authentication errors, never offline.
    """


class _MissingEndpointError(CloudError):
    """HTTP 404 or 405: an older server without this endpoint."""


def _error(response: httpx.Response, what: str) -> CloudError:
    try:
        body = response.json()
    except Exception:
        body = None
    body = body if isinstance(body, dict) else {}
    message = body.get("message") or f"{what} failed (HTTP {response.status_code})"
    # A 422 lists each problem by field
    errors = body.get("errors")
    if isinstance(errors, dict):
        details = [
            f"  - {field}: {text}"
            for field, texts in errors.items()
            for text in (texts if isinstance(texts, list) else [texts])
            if str(text) not in str(message)
        ]
        if details:
            message = "\n".join([str(message), *details])
    if response.status_code in (404, 405):
        return _MissingEndpointError(str(message))
    if response.status_code >= 500:
        return CloudOfflineError(str(message))
    return CloudError(str(message))


def api(
    path: str,
    token: str,
    url: str,
    method: str = "GET",
    body: Mapping[str, Any] | None = None,
) -> Any:
    """Call the cloud API and return the decoded JSON."""
    return _request(path, token, url, method, body).json()


def _request(
    path: str,
    token: str,
    url: str,
    method: str = "GET",
    body: Mapping[str, Any] | None = None,
) -> httpx.Response:
    try:
        with _client() as client:
            response = client.request(
                method,
                f"{url}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as error:
        raise CloudOfflineError(f"Could not reach {host(url)}: {error}") from error
    if response.is_error:
        raise _error(response, "Cloud request")
    return response


def resolve_choices(
    body: Mapping[str, Any], token: str, url: str
) -> tuple[dict[str, Any], str | None]:
    """Resolve project choices on the cloud: the envelope and the defaults revision.

    Each Framework-Notice header is shown as a message.
    """
    response = _request("/api/v2/resolve", token, url, method="POST", body=body)
    for notice in response.headers.get_list("Framework-Notice"):
        inform(notice)
    envelope = response.json()
    if not isinstance(envelope, dict):
        raise CloudError("The server returned an unexpected project definition.")
    return envelope, response.headers.get("Framework-Defaults-Revision")


# Logging in ---------------------------------------------------------------------------


def consent_notice(url: str) -> str:
    return (
        f"Connecting this machine to {url} enables cloud features\n"
        "that send data to that service when you use them:\n"
        "  - settings sync: your Framework settings document (cloud_sync())\n"
        "  - project registration: project name, type, and language (new())\n"
        "  - integrity ledger: file digests, sizes, and this machine's hostname\n"
        "    (data_save(); opt out any time with FW_LEDGER=off)\n"
        "  - publishing: documents you explicitly publish()\n"
        "Your data files themselves are never uploaded unless you publish them.\n"
        f"Details: {url}/privacy\n"
    )


# Tests replace the pause between approval checks.
_sleep = time.sleep


def _device_login(server: str, browser: bool) -> str:
    """Approve this machine in the browser and return the new token.

    The same flow as ``fw login`` and Stata's ``fw_login``: the server issues a
    short code, you approve it on the server's page, and the token comes back
    here. Nothing is typed or pasted.
    """
    name = host(server)
    try:
        with _client() as client:
            response = client.post(
                f"{server}/api/v1/cli/device", json={"client_name": f"Python on {platform.node()}"}
            )
    except httpx.HTTPError as error:
        raise CloudOfflineError(f"Could not reach {name}: {error}") from error
    if response.status_code in (404, 405):
        raise CloudError(
            f"{name} does not offer browser login. Create a token at {server}/tokens, "
            "then call cloud_login(token)."
        )
    if response.is_error:
        raise _error(response, "Browser login")
    start = response.json()
    code = start["device_code"]
    page = start.get("verification_url_complete") or start["verification_url"]
    interval = int(start.get("interval") or 5)
    expires = int(start.get("expires_in") or 600)
    print(
        f"Approve this computer in your browser.\n  Code: {start.get('user_code', '')}\n"
        f"  Page: {page}\nWaiting for approval. Press Ctrl+C to stop.",
        file=sys.stderr,
    )
    if browser:
        # A missing browser only means opening the link by hand.
        with contextlib.suppress(Exception):
            webbrowser.open(page)
    waited = 0
    while waited < expires:
        _sleep(interval)
        waited += interval
        try:
            with _client() as client:
                response = client.post(
                    f"{server}/api/v1/cli/device/token", json={"device_code": code}
                )
        except httpx.HTTPError as error:
            raise CloudOfflineError(f"Could not reach {name}: {error}") from error
        if response.status_code == 428:
            continue
        if response.status_code == 429:
            interval += 5
            continue
        if response.status_code == 403:
            raise CloudError("The login request was denied in the browser.")
        if response.status_code == 410:
            break
        if response.is_error:
            raise _error(response, "Browser login")
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            raise CloudError(f"{name} approved the login but returned no token.")
        return token
    raise CloudError("The login code expired. Run cloud_login() again.")


def cloud_login(
    token: str | None = None,
    url: str | None = None,
    verify: bool = True,
    consent: bool | None = None,
    browser: bool = True,
) -> str:
    """Connect this machine to a Framework Cloud server.

    Without a token, ``cloud_login()`` opens the server's approval page in your
    browser. Sign in, or create a free account, and approve this computer; the
    token comes back here. With a token from the server's token page,
    ``cloud_login(token)`` stores that token instead.

    The token is stored per server in the system keyring (the same entry R,
    Stata, and ``fw`` use), or in a file readable only by you when no keyring
    is available. The server becomes your default.

    Args:
        token: A personal token. Omit it to approve this computer in the browser.
        url: The server. Default: ``FW_CLOUD_URL``, then your saved default,
            then https://framework.pub.
        verify: Check the token with the server first.
        consent: ``True`` to confirm a pasted token without a prompt (scripts,
            CI). Approving in the browser is the confirmation for browser login.
        browser: Open the approval page. ``False`` prints the link only.

    Returns:
        Where the token was stored: ``"keyring"`` or a file path.
    """
    show_messages()
    server = cloud_server(url).url
    name = host(server)
    if token is None:
        print(consent_notice(server), file=sys.stderr)
        token = _device_login(server, browser)
        consent = True
    if len(token) < 10:
        raise CloudError("That does not look like a Framework Cloud token.")
    if consent is not True:
        print(consent_notice(server), file=sys.stderr)
        if not sys.stdin.isatty():
            raise CloudError(
                "cloud_login() needs confirmation. In a non-interactive session, call "
                "cloud_login(token, consent=True) after reviewing what connecting shares "
                f"(printed above; {server}/privacy)."
            )
        answer = input(f"Connect this machine to {name}? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            raise CloudError("Cancelled - this machine was not connected.")

    if verify:
        me = api("/api/v1/me", token, server)
        inform(f"[ok] Token verified on {name}: {me.get('email') or me.get('project') or 'ok'}")

    override = os.environ.get("FW_TOKEN_FILE")
    if override:
        path = Path(override).expanduser()
        _write_token_file(path, token)
        stored = str(path)
        inform(f"[ok] Token saved to {path} (FW_TOKEN_FILE)")
    elif _keyring_set(keyring_service(server), token):
        stored = "keyring"
        inform(f"[ok] Token stored in your system keyring (service: {keyring_service(server)})")
    else:
        path = ensure_config_dir() / token_file_name(server)
        _write_token_file(path, token)
        stored = str(path)
        inform(f"[ok] Token saved to {path}")

    _yaml.write(ensure_config_dir() / "cloud.yml", {"server": server})
    inform(f"[ok] Default server: {server}")
    legacy = _legacy_token_file()
    if not override and is_default(server) and legacy.exists():
        legacy.unlink()
        inform(f"[ok] Removed legacy token file {legacy}")
    return stored


def _revoke(server: str, token: str) -> None:
    """Revoke a token on the server. A token the server already rejects is fine."""
    try:
        with _client() as client:
            response = client.delete(
                f"{server}/api/v1/token", headers={"Authorization": f"Bearer {token}"}
            )
        if response.status_code == 401 or not response.is_error:
            return
    except httpx.HTTPError:
        pass
    inform(
        f"The token could not be revoked on {host(server)}. "
        f"Revoke it on the tokens page: {server}/tokens"
    )


def cloud_logout(url: str | None = None) -> list[str]:
    """Revoke and remove this machine's stored token for a server. Returns what was removed.

    Each stored token is revoked on the server first. If the server can't be reached, the
    local copy is still removed, and you can revoke the token on the server's tokens page.
    The keyring entry, the token file, and the file ``FW_TOKEN_FILE`` names are removed. For
    the default server, the legacy keyring entry and ``~/.secrets/framework/token`` are removed
    too. A token in ``FW_CLOUD_TOKEN`` is neither revoked nor removed, and stays in effect
    until you unset it.

    Args:
        url: The server. Default: ``FW_CLOUD_URL``, then your saved default, then
            https://framework.pub.

    Returns:
        A description of each keyring entry and the path of each file removed. The list is
        empty when no token was stored.
    """
    show_messages()
    server = cloud_server(url).url
    removed = []
    services = [keyring_service(server)] + ([LEGACY_SERVICE] if is_default(server) else [])
    paths = [config_dir() / token_file_name(server)]
    if is_default(server):
        paths.append(_legacy_token_file())
    if os.environ.get("FW_TOKEN_FILE"):
        # cloud_login() writes here when it is set, and cloud_token() reads it first
        paths.insert(0, Path(os.environ["FW_TOKEN_FILE"]).expanduser())
    # Only tokens this call removes are revoked, never one in FW_CLOUD_TOKEN
    stored = [_keyring_get(service) for service in services]
    stored += [_read_token_file(path) for path in paths]
    for token in dict.fromkeys(t for t in stored if t):
        _revoke(server, token)
    for service in services:
        if _keyring_delete(service):
            removed.append(f"keyring ({service})")
    for path in paths:
        if path.exists():
            path.unlink()
            removed.append(str(path))
    if removed:
        inform(f"[ok] Removed: {', '.join(removed)}")
    else:
        inform(f"No stored token found for {host(server)}.")
    if os.environ.get("FW_CLOUD_TOKEN"):
        inform("FW_CLOUD_TOKEN is still set, so this machine stays connected until you unset it.")
    return removed


def cloud_status(url: str | None = None) -> bool:
    """Report the server in use and whether this machine is connected to it.

    The server, where that setting came from, and the account are printed. When the project
    publishes to a different server, that server is printed too.

    Args:
        url: The server. Default: ``FW_CLOUD_URL``, then your saved default, then
            https://framework.pub.

    Returns:
        True when a token is stored and the server accepts it. False when no token is stored,
        the server is unreachable, or the token is invalid.
    """
    show_messages()
    server = cloud_server(url)
    inform(f"Server: {server.url} (from {server.source})")
    if project_token() is not None:
        project = cloud_server(scope="project")
        if project.url != server.url:
            inform(f"This project publishes to {project.url} (from {project.source})")
    token = cloud_token(server.url)
    if token is None:
        login = (
            "cloud_login()" if is_default(server.url) else f'cloud_login(token, url="{server.url}")'
        )
        inform(f"Not connected. Run {login} to connect.")
        return False
    try:
        me = api("/api/v1/me", token, server.url)
    except CloudError as error:
        inform(
            f"Token found but {host(server.url)} is unreachable or the token is invalid: {error}"
        )
        return False
    inform(f"Connected to {host(server.url)} as {me.get('email') or me.get('project')}")
    return True


def cloud_settings() -> Any:
    """Your settings document from the cloud."""
    server = cloud_server().url
    return api("/api/v1/settings", _require_token(server), server)


# Settings sync ------------------------------------------------------------------------


def _sync_paths() -> tuple[Path, Path]:
    folder = config_dir()
    return folder / "cloud-settings.yml", folder / "sync-state.yml"


def _document_hash(document: Any) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cloud_sync(direction: str = "auto") -> int:
    """Sync your settings document with the cloud. Returns the revision.

    ``"pull"`` takes the cloud version (backing up local edits), ``"push"``
    sends your local mirror, and ``"auto"`` picks the side that changed and
    stops when both did. The mirror is ``cloud-settings.yml`` in
    ``config_dir()``.

    Args:
        direction: One of "auto", "pull", or "push". Default: "auto". With "auto", a pull
            happens when there is no local mirror or sync state yet.

    Returns:
        The settings revision after the sync.

    Raises:
        CloudError: When ``direction`` is not allowed, no token is stored, both sides changed
            under "auto", a push has no local mirror or sync state, or the server rejects a
            push.
    """
    show_messages()
    if direction not in ("auto", "pull", "push"):
        raise CloudError("direction must be auto, pull, or push")
    server = cloud_server().url
    token = _require_token(server)
    document_path, state_path = _sync_paths()
    remote = api("/api/v1/settings", token, server)
    revision = int(remote.get("revision") or 0)
    state = _yaml.read(state_path) if state_path.is_file() else None
    local = _yaml.read(document_path) if document_path.is_file() else None
    local_changed = (
        local is not None and state is not None and _document_hash(local) != state.get("hash")
    )
    remote_ahead = state is None or revision > int(state.get("revision", -1))

    if direction == "auto":
        if local is None or state is None:
            direction = "pull"
        elif local_changed and remote_ahead:
            raise CloudError(
                "Both your local mirror and the cloud changed since the last sync.\n"
                '  cloud_sync("pull") takes the cloud version and backs up your file.\n'
                '  Then copy your edits from the backup and run cloud_sync("push").'
            )
        elif local_changed:
            direction = "push"
        elif remote_ahead:
            direction = "pull"
        else:
            inform(f"[ok] Already in sync (revision {revision})")
            return revision

    if direction == "pull":
        if local is not None and local_changed:
            backup = document_path.with_name(
                f"{document_path.name}.backup-{datetime.now():%Y%m%d-%H%M%S}"
            )
            shutil.copy2(document_path, backup)
            inform(f"Local edits backed up to {backup}")
        ensure_config_dir()
        _yaml.write(document_path, remote.get("document"))
        _yaml.write(
            state_path, {"revision": revision, "hash": _document_hash(remote.get("document"))}
        )
        inform(f"[ok] Pulled cloud settings (revision {revision}) -> {document_path}")
        return revision

    if local is None:
        raise CloudError('Nothing to push: no local mirror. Run cloud_sync("pull") first.')
    if state is None:
        raise CloudError(
            'No sync state. Run cloud_sync("pull") first so pushes can detect conflicts.'
        )
    body = {
        "document": local,
        "base_revision": state.get("revision"),
        "client": {"device": platform.node(), "app": "framework-py", "version": _version()},
    }
    try:
        result = api("/api/v1/settings", token, server, method="PUT", body=body)
    except CloudError as error:
        raise CloudError(
            f"Push rejected: {error}\nThe cloud likely changed since your last sync. "
            'cloud_sync("pull") first.'
        ) from error
    new_revision = int(result.get("revision") or 0)
    _yaml.write(state_path, {"revision": new_revision, "hash": _document_hash(local)})
    inform(f"[ok] Pushed settings (revision {new_revision})")
    return new_revision


# The integrity ledger -----------------------------------------------------------------


def _ledger_live() -> bool:
    from framework.settings import project_dotenv

    mode = os.environ.get("FW_LEDGER", "") or project_dotenv().get("FW_LEDGER", "")
    if not mode:
        try:
            from framework.settings import settings_read

            options = settings_read().get("options") or {}
            mode = str(options.get("cloud_ledger") or "") if isinstance(options, dict) else ""
        except FrameworkError:
            mode = ""
    return mode.lower() in ("live", "on", "true")


def ledger_push_quietly(name: str, digest: str, file_path: Path | None = None) -> Any:
    """Send one digest to the project's ledger after ``data_save()``, in live mode only.

    No project token or an unreachable server means "local only", never a
    failed save. framework.db stays the record either way.
    """
    if not _ledger_live():
        return None
    token = project_token()
    if token is None:
        return None
    url = cloud_server(scope="project").url
    body = {
        "name": name,
        "algo": "sha256",
        "hash": digest,
        "size_bytes": file_path.stat().st_size if file_path and file_path.is_file() else None,
        # UTC with a Z, as ledger_push() sends: the server stores the clock time as UTC
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "client": {"device": platform.node(), "package_version": _version()},
    }
    try:
        result = api("/api/v1/ledger", token, url, method="POST", body=body)
    except CloudError as error:
        inform(f"Ledger push skipped: {error}")
        return None
    inform(f"[ok] Ledger entry #{result.get('sequence')} recorded on {host(url)}")
    return result


def _ledger_entries(name: str | None = None) -> list[dict[str, Any]]:
    token = _require_project_token()
    params = f"?{httpx.QueryParams({'name': name})}" if name else ""
    result = api(f"/api/v1/ledger{params}", token, cloud_server(scope="project").url)
    return list(result.get("entries") or [])


def ledger_push() -> dict[str, int]:
    """Send framework.db's file digests to the project's cloud ledger.

    Only names whose newest cloud entry differs are sent. Returns the counts
    pushed and already current.
    """
    show_messages()
    token = _require_project_token()
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT name, hash, updated_at FROM data WHERE hash IS NOT NULL AND hash != ''"
        ).fetchall()
    if not rows:
        inform("Local ledger is empty - nothing to push.")
        return {"pushed": 0, "up_to_date": 0}
    url = cloud_server(scope="project").url
    heads: dict[str, str] = {}
    for entry in _ledger_entries():
        heads.setdefault(entry["name"], entry["hash"])

    pushed = up_to_date = 0
    for row in rows:
        if heads.get(row["name"]) == row["hash"]:
            up_to_date += 1
            continue
        body: dict[str, Any] = {
            "name": row["name"],
            "algo": "sha256",
            "hash": row["hash"],
            "client": {
                "device": platform.node(),
                "package_version": _version(),
                "source": "ledger_push",
            },
        }
        recorded = db.parse_timestamp(row["updated_at"])
        if recorded is not None:
            body["recorded_at"] = recorded.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            api("/api/v1/ledger", token, url, method="POST", body=body)
        except CloudError as error:
            inform(f"  [skip] {row['name']}: {error}")
            continue
        inform(f"  [ok] pushed {row['name']}")
        pushed += 1
    inform(f"[ok] Ledger push complete: {pushed} pushed, {up_to_date} already current.")
    return {"pushed": pushed, "up_to_date": up_to_date}


def ledger_list(name: str | None = None) -> pd.DataFrame:
    """The project's cloud ledger, newest first.

    Args:
        name: Only list entries for this catalog name. Default: every entry.

    Returns:
        A DataFrame with columns sequence, name, hash, size_bytes, and recorded_at.

    Raises:
        CloudError: When the project has no token, or the server request fails.
    """
    columns = ["sequence", "name", "hash", "size_bytes", "recorded_at"]
    rows = [{key: entry.get(key) for key in columns} for entry in _ledger_entries(name)]
    return pd.DataFrame(rows, columns=columns)


def data_verify(name: str) -> bool:
    """Check a catalog file against framework.db and, if connected, the cloud ledger.

    Returns True when every record matches. A mismatch issues a warning.

    The file's SHA-256 digest is compared with its framework.db record and, when the project
    has a token, with the newest cloud ledger entry for the name. A missing record, an
    unreachable ledger, or a name with no ledger entries is reported but does not count as a
    mismatch.

    Args:
        name: A catalog name or file path, as accepted by ``data_info()``.

    Returns:
        True when every available record matches the file, otherwise False.

    Raises:
        DataNotFoundError: When the name is not in the catalog.
        FrameworkError: When the file does not exist.
    """
    import warnings

    from framework.catalog import data_info

    show_messages()
    info = data_info(name)
    if not info.path.is_file():
        raise FrameworkError(f"File missing: {info.path}")
    digest = file_hash(info.path)
    ok = True
    inform(f"Verifying {name} ({info.path})")
    inform(f"  file sha256: {digest[:16]}...")

    # A file path finds the record saved under its catalog name, by its stored path
    from framework._paths import relative_to_root
    from framework.project import project_root

    stored = relative_to_root(info.path, project_root())
    record = db.get_data_record(name, path=stored)
    if record is not None and record.hash:
        if record.hash == digest:
            inform("  [ok] matches local framework.db record")
        else:
            inform(f"  [MISMATCH] local framework.db has {record.hash[:16]}...")
            ok = False
    else:
        inform("  (no framework.db record for this file yet)")
    ledger_name = record.name if record is not None else name

    if project_token() is not None:
        try:
            entries = _ledger_entries(ledger_name)
        except CloudError as error:
            inform(f"  (cloud ledger unreachable: {error})")
            entries = None
        if entries:
            head = entries[0]
            if head.get("hash") == digest:
                inform(
                    f"  [ok] matches cloud ledger entry #{head.get('sequence')} "
                    f"(recorded {head.get('recorded_at') or '?'})"
                )
            else:
                inform(
                    f"  [MISMATCH] cloud ledger head #{head.get('sequence')} has "
                    f"{str(head.get('hash'))[:16]}..."
                )
                ok = False
        elif entries is not None:
            inform("  (no cloud ledger entries for this name yet)")

    if ok:
        inform("[ok] Verified")
    else:
        warnings.warn(f"Integrity mismatch for '{name}'", FrameworkWarning, stacklevel=2)
    return ok


# Publishing ---------------------------------------------------------------------------


def _zip(folder: Path, target: Path) -> int:
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    if not files:
        raise CloudError(f"Nothing to bundle in {folder}")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(folder).as_posix())
    return len(files)


def _publish_collection(
    collection: str | int,
    token: str,
    url: str,
    public: bool | None,
    share_unlisted: bool,
) -> dict[str, Any]:
    if isinstance(collection, bool) or not isinstance(collection, (str, int)):
        raise CloudError("collection must be a collection ID or slug.")
    rows = api("/api/v2/collections", token, url)["collections"]
    matches = [
        row for row in rows if str(row["id"]) == str(collection) or row["slug"] == str(collection)
    ]
    if len(matches) != 1:
        raise CloudError(f"Collection {collection!r} is missing or ambiguous in this project.")
    selected: dict[str, Any] = matches[0]
    if selected["visibility"] == "public" and public is False:
        raise CloudError("Public collections require public artifacts. Set visibility='public'.")
    if selected["visibility"] == "unlisted" and public is False and not share_unlisted:
        raise CloudError(
            "Pass share_unlisted=True to share an unlisted artifact with collection readers."
        )
    return selected


def cloud_publish(
    source: Path,
    dest: str | None = None,
    self_contained: bool = True,
    slug: str | bool | None = None,
    title: str | None = None,
    include_private: bool = False,
    description: str | None = None,
    kind: str | None = None,
    password: str | None = None,
    visibility: str | None = None,
    collection: str | int | None = None,
    share_unlisted: bool = False,
) -> str:
    """Publish a file, rendered HTML folder, or notebook to the project cloud server.

    Accepts a self-contained ``.html``, an ``.html`` with a sibling
    ``<name>_files/`` folder, a render output folder, or a ``.qmd`` or ``.ipynb``. Each
    publish adds a version. A string ``slug`` names the document and leaves its
    visibility alone: a new artifact is unlisted, and an existing one keeps its setting.
    ``True`` turns the readable URL on with the derived name, and ``False`` turns it off.
    Pass ``visibility="public"`` to make an artifact public. An explicit ``visibility``
    always wins over ``True`` and ``False``. A source with "private" in its path needs
    confirmation, or ``include_private=True``.
    """
    sources = [source]
    if source.is_dir():
        sources = sorted(p for p in source.rglob("*") if p.is_file())
    elif source.with_name(f"{source.stem}_files").is_dir():
        assets = source.with_name(f"{source.stem}_files")
        sources += sorted(p for p in assets.rglob("*") if p.is_file())
    confirm_private_upload(sources, include_private)
    token = _require_project_token()
    url = cloud_server(scope="project").url
    # An explicit visibility wins. ``readable`` is the older switch between public
    # and unlisted, sent only for slug=True or False. A string slug never sets it.
    readable = slug if isinstance(slug, bool) and visibility is None else None
    public = visibility == "public" if visibility is not None else readable
    selected = (
        None
        if collection is None
        else _publish_collection(collection, token, url, public, share_unlisted)
    )
    with tempfile.TemporaryDirectory(prefix="fw_publish_") as temp:
        stage = Path(temp)
        html: Path | None = None
        bundle: Path | None = None
        entrypoint: str | None = None
        suffix = source.suffix.lower()

        if source.is_dir():
            bundle = source
            pages = sorted(
                p.name for p in source.iterdir() if p.suffix.lower() in (".html", ".htm")
            )
            if not pages:
                raise CloudError(f"No HTML file found at the top of {source}")
            entrypoint = "index.html" if "index.html" in pages else pages[0]
        elif suffix in (".qmd", ".ipynb"):
            quarto = shutil.which("quarto")
            if quarto is None:
                raise CloudError("Rendering requires the quarto command on your PATH.")
            out = stage / "render"
            command = [
                quarto,
                "render",
                str(source.resolve()),
                "--output-dir",
                str(out),
                "--to",
                "html",
            ]
            if self_contained:
                command.append("--embed-resources")
            if subprocess.run(command, capture_output=True, check=False).returncode != 0:
                raise CloudError("Quarto render failed.")
            # A project's _quarto.yml keeps the notebook's folder under the output folder
            page = rendered_page(out, source.stem)
            if page is None:
                raise CloudError("Quarto render produced no HTML output.")
            if self_contained:
                html = page
            else:
                bundle, entrypoint = page.parent, page.name
        elif suffix in (".html", ".htm"):
            assets = source.with_name(f"{source.stem}_files")
            if assets.is_dir():
                bundle = stage / "bundle"
                bundle.mkdir()
                shutil.copy2(source, bundle / source.name)
                shutil.copytree(assets, bundle / assets.name)
                entrypoint = source.name
            else:
                html = source
        else:
            html = source

        name = slug if isinstance(slug, str) else (dest or source.stem)
        data: dict[str, str] = {"slug": re.sub(r"\.html?$", "", name)}
        if readable is not None:
            data["readable"] = "1" if readable else "0"
        for key, value in (
            ("title", title),
            ("description", description),
            ("kind", kind),
            ("password", password),
            ("visibility", visibility),
        ):
            if value is not None:
                data[key] = value
        if bundle is not None:
            archive = stage / "bundle.zip"
            count = _zip(bundle, archive)
            inform(f"Publishing bundle ({count} files)")
            data["entrypoint"] = str(entrypoint)
            upload = ("bundle", archive, "application/zip")
        else:
            assert html is not None
            upload = (
                "file",
                html,
                mimetypes.guess_type(html.name)[0] or "application/octet-stream",
            )

        url = cloud_server(scope="project").url
        field, path, content_type = upload
        try:
            with _client() as client, path.open("rb") as handle:
                response = client.post(
                    f"{url}/api/v1/documents",
                    data=data,
                    files={field: (path.name, handle, content_type)},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as error:
            raise CloudError(f"Could not reach {host(url)}: {error}") from error
    if response.is_error:
        raise _error(response, "Publish")
    result = response.json()
    if result.get("version") is not None:
        inform(f"Published version {result['version']}: {result.get('url')}")
    else:
        inform(f"Published: {result.get('url')}")
    # The server's notice assumes an unlisted link. Say it plainly for a public
    # artifact whose owner has no handle yet.
    notice = str(result.get("notice") or "")
    public = result.get("visibility") == "public" or (
        result.get("visibility") is None and "handle" in notice
    )
    if public and not result.get("readable_url"):
        inform("Public. Set a handle in Account > Account details to get a readable URL.")
    elif notice and "handle" not in notice:
        inform(notice)
    if selected is not None:
        try:
            if not result.get("id"):
                raise CloudError("The server did not return an artifact ID.")
            api(
                f"/api/v2/collections/{selected['id']}/items",
                token,
                url,
                method="POST",
                body={"document_id": result["id"], "share_unlisted": share_unlisted},
            )
            inform(f"Added to collection: {selected['title']}")
        except Exception as error:
            warnings.warn(
                f"Artifact published at {result.get('url')}, but collection "
                f"attachment failed: {error}. Attach it from the project Outputs page; "
                "publishing again creates another version.",
                FrameworkWarning,
                stacklevel=2,
            )
    return str(result.get("url"))


# Projects ---------------------------------------------------------------------------


def write_project_token(project_dir: Path, token: str, url: str) -> list[str]:
    """Add the project's key and server to its ``.env`` (never to the settings).

    Git is made to ignore ``.env`` first, so the token can't be committed.
    """
    from framework.create import keep_env_out_of_git

    env = project_dir / ".env"
    present = dotenv.read(env)
    missing = {
        k: v for k, v in (("FW_PROJECT_TOKEN", token), ("FW_CLOUD_URL", url)) if k not in present
    }
    if not missing:
        return []
    keep_env_out_of_git(project_dir)
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    if text:
        text += "\n"
    text += "# Framework Cloud project key and server (created by framework.new)\n"
    text += "".join(f"{key}={value}\n" for key, value in missing.items())
    env.write_text(text, encoding="utf-8")
    inform(f"  Added: {' and '.join(missing)} to .env")
    return list(missing)


def registration_body(
    name: str,
    type: str,
    spec: Mapping[str, Any],
    package_sets: Sequence[str] = (),
    skill_sets: Sequence[str] = (),
) -> dict[str, Any]:
    """The choices a project built offline was made from, for ``/api/v2/projects``.

    Each of Framework's five types is a structure of the same name.
    """
    ai = spec.get("ai") or {}
    body: dict[str, Any] = {
        "name": name,
        "language": "python",
        "structure": f"framework/{type}",
        "tools": dict(spec.get("tools") or {}),
        "settings": {
            "sensitive": (spec.get("security") or {}).get("sensitive") is True,
            "git": (spec.get("git") or {}).get("use_git") is not False,
            "ai": {
                "enabled": ai.get("enabled") is True,
                "assistants": dict(ai.get("assistants") or {}),
            },
        },
    }
    if package_sets:
        body["package_sets"] = [s if "/" in s else f"framework/{s}" for s in package_sets]
    if skill_sets:
        body["skill_sets"] = [s if "/" in s else f"framework/{s}" for s in skill_sets]
    return body


def register_project(
    project_dir: Path,
    name: str,
    type: str,
    spec: Mapping[str, Any] | None = None,
    package_sets: Sequence[str] = (),
    skill_sets: Sequence[str] = (),
) -> Any:
    """Register a new local project on the cloud when logged in. Never fails creation.

    The cloud records the structure, tools, sets, and settings the project
    was built from. A server without ``/api/v2/projects`` gets the older
    registration.
    """
    server = cloud_server().url
    token = cloud_token(server)
    if token is None:
        return None
    try:
        if spec is not None:
            body = registration_body(name, type, spec, package_sets, skill_sets)
            try:
                result = api("/api/v2/projects", token, server, method="POST", body=body)
                slug, project_token = result.get("slug"), result.get("token")
            except _MissingEndpointError:
                spec = None
        if spec is None:
            body = {"name": name, "project_type": type, "language": "python"}
            result = api("/api/v1/projects", token, server, method="POST", body=body)
            slug, project_token = result["project"]["slug"], result["pull_token"]
    except CloudError as error:
        inform(f"Cloud project registration skipped: {error}")
        return None
    inform(f"[ok] Registered on {host(server)}: {slug}")
    if project_token:
        write_project_token(project_dir, str(project_token), server)
    return result


def fetch_layers(key: str, url: str | None = None) -> tuple[dict[str, Any], str]:
    """A project's layers file from the cloud, by its project token."""
    server = cloud_server(url).url
    inform(f"Fetching this project's definition from {server} (what is shared: {server}/privacy)")
    envelope = api("/api/v2/projects/pull", key, server)
    if not isinstance(envelope, dict):
        raise CloudError("The server returned an unexpected project definition.")
    return envelope, server
