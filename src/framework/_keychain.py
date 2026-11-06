"""macOS Keychain entries through ``/usr/bin/security``, as ``fw`` reads and writes them.

``fw`` writes each token with ``security`` and reads it through go-keyring,
which also runs ``security``. Those items trust only ``/usr/bin/security``,
so reading them in this process through the Security framework (what
Python's keyring package does) shows an "allow access" dialog, and a run
without a screen waits forever. Running the same command avoids the dialog.

Setting ``PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`` turns these
entries off, as it turns off the keyring package. Tests rely on it.
"""

from __future__ import annotations

import base64
import os
import subprocess

SECURITY = "/usr/bin/security"
NOT_FOUND = 44
_GO_KEYRING = "go-keyring-base64:"


def disabled() -> bool:
    return os.environ.get("PYTHON_KEYRING_BACKEND", "") == "keyring.backends.null.Keyring"


def _run(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [SECURITY, *args], input=stdin, capture_output=True, text=True, check=False
    )


def decode(output: str) -> str | None:
    """A password as ``security -w`` prints it, decoding go-keyring's base64 form."""
    value = output.rstrip("\n")
    if value.startswith(_GO_KEYRING):
        value = base64.b64decode(value[len(_GO_KEYRING) :]).decode("utf-8")
    return value or None


def get(service: str, account: str) -> str | None:
    """The password, or None when the item is missing or the keychain can't be read."""
    if disabled():
        return None
    try:
        result = _run(["find-generic-password", "-s", service, "-a", account, "-w"])
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return decode(result.stdout)


def set(service: str, account: str, password: str) -> bool:
    """Add or update an item. The command goes on stdin, so the password is never in argv."""
    if disabled():
        return False
    # The command is quoted for security's own parser, which has no escapes
    if any(c in value for value in (service, account, password) for c in "'\\\n\r"):
        return False
    command = f"add-generic-password -U -s '{service}' -a '{account}' -w '{password}'\n"
    try:
        _run(["-i"], stdin=command)
    except OSError:
        return False
    # security -i exits 0 even when a command fails, so read the item back
    return get(service, account) == password


def delete(service: str, account: str) -> bool:
    """Remove an item. False when it was missing or could not be removed."""
    if disabled():
        return False
    try:
        result = _run(["delete-generic-password", "-s", service, "-a", account])
    except OSError:
        return False
    return result.returncode == 0
