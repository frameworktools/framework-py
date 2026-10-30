"""Linux Secret Service entries in the format R's keyring package uses.

R's keyring package looks up items by the libsecret schema
``com.rstudio.keyring.password`` (the ``xdg:schema`` attribute) and the
``service`` and ``username`` attributes. Python's keyring package writes
items without ``xdg:schema`` and with an extra ``application`` attribute, so
R and ``fw`` cannot see them, and its replace adds a second item beside
theirs. Framework writes and deletes these items directly, the same way
``fw`` does, so each token has exactly one item that every client reads.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

SCHEMA = "com.rstudio.keyring.password"


def _attributes(service: str, username: str) -> dict[str, str]:
    return {"service": service, "username": username}


@contextmanager
def _collection() -> Iterator[Any]:
    import secretstorage

    connection = secretstorage.dbus_init()
    try:
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            collection.unlock()
        yield collection
    finally:
        connection.close()


def get(service: str, username: str) -> str | None:
    """The secret, preferring the item in R's schema when there are several."""
    with _collection() as collection:
        items = list(collection.search_items(_attributes(service, username)))
        if not items:
            return None
        items.sort(key=lambda item: item.get_attributes().get("xdg:schema") != SCHEMA)
        secret = items[0].get_secret()
    return bytes(secret).decode("utf-8") or None


def set(service: str, username: str, secret: str) -> None:
    """Replace every matching item with one item in R's schema."""
    attributes = _attributes(service, username)
    with _collection() as collection:
        for item in collection.search_items(attributes):
            item.delete()
        collection.create_item(
            f"{service}:{username}",
            {**attributes, "xdg:schema": SCHEMA},
            secret.encode("utf-8"),
        )


def delete(service: str, username: str) -> bool:
    """Delete every matching item. Returns whether there was one."""
    with _collection() as collection:
        items = list(collection.search_items(_attributes(service, username)))
        for item in items:
            item.delete()
    return bool(items)
