"""Windows Credential Manager entries in the format R's keyring package uses.

R's keyring package stores a generic credential with the target name
``:<service>:<username>`` and the secret as UTF-8 bytes. Python's keyring
package uses a different target and UTF-16 text, so Framework reads and
writes these entries directly, the same way ``fw`` does.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


def target(service: str, username: str) -> str:
    return f":{service}:{username}"


def decode(blob: bytes) -> str:
    """A secret blob as text: UTF-16LE when every second byte is zero, else UTF-8.

    Tokens are ASCII, so a blob written by Python's keyring package (UTF-16)
    has a zero in every odd position.
    """
    if len(blob) >= 2 and len(blob) % 2 == 0 and all(b == 0 for b in blob[1::2]):
        return blob.decode("utf-16-le")
    return blob.decode("utf-8")


def _api() -> tuple[Any, Any]:  # pragma: no cover (Windows only)
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    advapi.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIAL)),
    ]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredDeleteW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    return advapi, CREDENTIAL


def get(service: str, username: str) -> str | None:  # pragma: no cover (Windows only)
    if sys.platform != "win32":
        return None
    advapi, credential = _api()
    pointer = ctypes.POINTER(credential)()
    if not advapi.CredReadW(target(service, username), CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        return None
    try:
        entry = pointer.contents
        blob = ctypes.string_at(entry.CredentialBlob, entry.CredentialBlobSize)
        return decode(blob)
    finally:
        advapi.CredFree(pointer)


def set(service: str, username: str, secret: str) -> None:  # pragma: no cover (Windows only)
    advapi, credential = _api()
    blob = secret.encode("utf-8")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    entry = credential()
    entry.Type = CRED_TYPE_GENERIC
    entry.TargetName = target(service, username)
    entry.CredentialBlobSize = len(blob)
    entry.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    entry.Persist = CRED_PERSIST_LOCAL_MACHINE
    entry.UserName = username
    if not advapi.CredWriteW(ctypes.byref(entry), 0):
        raise OSError(ctypes.get_last_error(), "CredWriteW failed")  # type: ignore[attr-defined]


def delete(service: str, username: str) -> bool:  # pragma: no cover (Windows only)
    advapi, _ = _api()
    return bool(advapi.CredDeleteW(target(service, username), CRED_TYPE_GENERIC, 0))
