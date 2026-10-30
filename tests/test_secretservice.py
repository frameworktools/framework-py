"""Linux Secret Service items in R's keyring format, against a fake service."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from framework import _secretservice, cloud


class FakeItem:
    def __init__(self, store: list[FakeItem], attributes: dict[str, str], secret: bytes) -> None:
        self.store, self.attributes, self.secret = store, attributes, secret

    def get_attributes(self) -> dict[str, str]:
        return dict(self.attributes)

    def get_secret(self) -> bytes:
        return self.secret

    def delete(self) -> None:
        self.store.remove(self)


class FakeCollection:
    def __init__(self) -> None:
        self.items: list[FakeItem] = []
        self.locked = True

    def is_locked(self) -> bool:
        return self.locked

    def unlock(self) -> None:
        self.locked = False

    def search_items(self, attributes: dict[str, str]) -> Any:
        # The real service yields lazily, so deleting while iterating must work.
        return iter([i for i in self.items if attributes.items() <= i.attributes.items()])

    def create_item(self, label: str, attributes: dict[str, str], secret: bytes) -> FakeItem:
        item = FakeItem(self.items, attributes, secret)
        self.items.append(item)
        return item

    def add(self, attributes: dict[str, str], secret: str) -> None:
        self.create_item("", attributes, secret.encode())


@pytest.fixture
def collection(monkeypatch: pytest.MonkeyPatch) -> FakeCollection:
    fake = FakeCollection()
    module = types.ModuleType("secretstorage")
    module.dbus_init = lambda: types.SimpleNamespace(close=lambda: None)  # type: ignore[attr-defined]
    module.get_default_collection = lambda connection: fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "secretstorage", module)
    return fake


SERVICE = "framework-cloud:framework.pub"
R_ITEM = {"service": SERVICE, "username": "token", "xdg:schema": _secretservice.SCHEMA}
PYTHON_KEYRING_ITEM = {
    "application": "Python keyring library",
    "service": SERVICE,
    "username": "token",
}


def test_set_replaces_every_match_with_one_item_in_r_schema(collection: FakeCollection) -> None:
    collection.add(R_ITEM, "fw_old_r")
    collection.add(PYTHON_KEYRING_ITEM, "fw_old_python")
    collection.add({"service": "other", "username": "token"}, "fw_other")
    _secretservice.set(SERVICE, "token", "fw_new")
    assert collection.locked is False
    matches = [i for i in collection.items if i.attributes["service"] == SERVICE]
    assert [(m.attributes, m.secret) for m in matches] == [(R_ITEM, b"fw_new")]
    assert len(collection.items) == 2


def test_get_prefers_the_r_schema_item(collection: FakeCollection) -> None:
    assert _secretservice.get(SERVICE, "token") is None
    collection.add(PYTHON_KEYRING_ITEM, "fw_python")
    assert _secretservice.get(SERVICE, "token") == "fw_python"
    collection.add(R_ITEM, "fw_r")
    assert _secretservice.get(SERVICE, "token") == "fw_r"


def test_delete_removes_every_match(collection: FakeCollection) -> None:
    collection.add(R_ITEM, "a")
    collection.add(PYTHON_KEYRING_ITEM, "b")
    assert _secretservice.delete(SERVICE, "token") is True
    assert collection.items == []
    assert _secretservice.delete(SERVICE, "token") is False


def test_cloud_uses_the_secret_service_on_linux(
    collection: FakeCollection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    collection.add(PYTHON_KEYRING_ITEM, "fw_stale")
    assert cloud._keyring_set(SERVICE, "fw_token") is True
    assert [i.attributes for i in collection.items] == [R_ITEM]
    assert cloud._keyring_get(SERVICE) == "fw_token"
    assert cloud._keyring_delete(SERVICE) is True
    assert collection.items == []
