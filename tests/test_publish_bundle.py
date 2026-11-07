"""Portable bundle delivery and access control boundaries."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import framework as fw
from framework.publish_bundle import (
    Destination,
    artifact_slug,
    build_bundle,
    destination,
    folder_deliver,
    track_external,
)


def test_folder_table_versions_and_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = tmp_path / "published"
    config = {
        "publish": {
            "default": "lab",
            "destinations": {
                "lab": {
                    "driver": "folder",
                    "path": str(site),
                    "url": "https://lab.example/research",
                }
            },
        }
    }
    monkeypatch.setattr(
        importlib.import_module("framework.settings"), "settings_read", lambda: config
    )
    monkeypatch.setattr("framework.storage.settings_read", lambda: config)
    monkeypatch.setattr("framework.cloud.project_token", lambda: None)
    frame = pd.DataFrame({"value": [1.23456, 7.0], "name": ["</script><script>x</script>", "two"]})
    url = fw.publish(frame, slug="Model_One.v2", title="<Research>")
    assert url == "https://lab.example/research/model-one-v2/"
    target = site / "model-one-v2"
    manifest = json.loads((target / "artifact.json").read_text())
    assert manifest["schema"] == "framework.bundle/v1"
    assert manifest["representation"] == "representation.json"
    assert (target / "data.csv").is_file()
    page = (target / "index.html").read_text()
    assert "__FRAMEWORK_ARTIFACT__" not in page
    assert "&lt;Research&gt;" in page
    assert "</script><script>x</script>" not in page
    assert "fetch(" not in page
    for file in manifest["files"]:
        raw = (target / file["path"]).read_bytes()
        assert file["bytes"] == len(raw)
        assert file["sha256"] == hashlib.sha256(raw).hexdigest()
    first = (target / "v/1/representation.json").read_bytes()
    fw.publish(pd.DataFrame({"value": [9]}), slug="Model_One.v2")
    assert (target / "v/1/representation.json").read_bytes() == first
    assert (target / "v/2/index.html").is_file()
    assert json.loads((site / "index.json").read_text())[0]["slug"] == "model-one-v2"
    assert 'href="model-one-v2/"' in (site / "index.html").read_text()


@pytest.mark.parametrize(
    "options",
    [
        {"password": "secret123"},
        {"visibility": "unlisted"},
        {"collection": 1},
        {"visibility": "private"},
    ],
)
def test_reject_access_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    site = tmp_path / "site"
    config = {"publish": {"destinations": {"lab": {"driver": "folder", "path": str(site)}}}}
    monkeypatch.setattr(
        importlib.import_module("framework.settings"), "settings_read", lambda: config
    )
    with pytest.raises(fw.FrameworkError):
        fw.publish(pd.DataFrame({"a": [1]}), to="lab", **options)
    assert not site.exists()


def test_destination_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        importlib.import_module("framework.settings"),
        "settings_read",
        lambda: {
            "publish": {
                "default": "lab",
                "destinations": {"lab": {"driver": "folder", "path": "/lab"}},
            }
        },
    )
    assert destination(None).driver == "folder"  # type: ignore[union-attr]
    assert destination("cloud").driver == "cloud"  # type: ignore[union-attr]
    assert destination(None, "legacy") is None


def test_tracking_metadata_no_local_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = tmp_path / "bundle"
    manifest = build_bundle(pd.DataFrame({"x": [2]}), bundle, slug="study")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("framework.cloud.project_token", lambda: "project-token")
    monkeypatch.setattr(
        "framework.cloud.cloud_server", lambda **kw: SimpleNamespace(url="https://cloud")
    )

    def api(path: str, token: str, server: str, **kwargs: Any) -> dict[str, Any]:
        if path == "/api/v1/me":
            return {"project": "project-one"}
        assert path == "/api/v1/projects/project-one/artifacts/external"
        calls.append(kwargs["body"])
        return {}

    monkeypatch.setattr("framework.cloud.api", api)
    receipt = folder_deliver(bundle, tmp_path / "private-share")
    track_external(manifest, receipt, bundle, "private")
    assert len(calls) == 1
    assert calls[0]["driver"] == "folder"
    assert calls[0]["url"] is None
    assert "location" not in calls[0]
    assert "private-share" not in json.dumps(calls)
    assert "representation" not in calls[0]


def test_tracking_failure_preserves_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    manifest = build_bundle(pd.DataFrame({"x": [2]}), bundle)
    receipt = folder_deliver(bundle, tmp_path / "site")
    monkeypatch.setattr("framework.cloud.project_token", lambda: "token")
    monkeypatch.setattr(
        "framework.cloud.cloud_server", lambda **kw: (_ for _ in ()).throw(RuntimeError("offline"))
    )
    with pytest.warns(fw.FrameworkWarning, match="Published successfully"):
        track_external(manifest, receipt, bundle, None)
    assert (tmp_path / "site/table/index.html").is_file()


def test_symlinks_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("hello")
    try:
        (source / "alias").symlink_to(source / "index.html")
    except OSError:
        pytest.skip("Symbolic links require elevated permissions on this platform.")
    with pytest.raises(fw.FrameworkError, match="symbolic"):
        build_bundle(source, tmp_path / "bundle")


def test_new_project_alias() -> None:
    assert fw.new is fw.new_project
    assert fw.new_project.__name__ == "new_project"
    assert artifact_slug("A_B.C -- D") == "a-b-c-d"


def test_private_github_requires_verified_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from framework.publish_bundle import validate

    monkeypatch.setattr("framework._pages.private_pages", lambda: False)
    with pytest.raises(fw.FrameworkError, match="confirmed"):
        validate(Destination("github", "github", {}), "private", None, None)


def test_named_s3_bundle_versions_and_private_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    from framework.publish_bundle import _s3_deliver

    objects: dict[str, bytes] = {}
    uploads: list[tuple[str, dict[str, str]]] = []

    class MissingError(Exception):
        def __init__(self) -> None:
            self.response = {"Error": {"Code": "NoSuchKey"}}

    class Client:
        def get_bucket_policy_status(self, **kwargs: Any) -> dict[str, Any]:
            return {"PolicyStatus": {"IsPublic": False}}

        def get_paginator(self, operation: str) -> Any:
            return self

        def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "Contents": [
                        {"Key": name} for name in objects if name.startswith(kwargs["Prefix"])
                    ]
                }
            ]

        def upload_file(self, path: str, bucket: str, key: str, **kwargs: Any) -> None:
            objects[key] = Path(path).read_bytes()
            uploads.append((key, kwargs["ExtraArgs"]))

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["Key"] not in objects:
                raise MissingError()
            return {"Body": io.BytesIO(objects[kwargs["Key"]])}

        def put_object(self, **kwargs: Any) -> None:
            objects[kwargs["Key"]] = kwargs["Body"]
            assert kwargs["ACL"] == "private"

    client = Client()
    bucket = SimpleNamespace(
        bucket="actual-bucket", client=lambda: client, key=lambda name: "project/" + name
    )
    monkeypatch.setattr("framework.storage.resolve_bucket", lambda name: bucket)
    bundle = tmp_path / "bundle"
    build_bundle(pd.DataFrame({"x": [1]}), bundle, slug="a")
    selected = Destination("archive", "s3", {"bucket": "results", "visibility": "private"})
    first = _s3_deliver(bundle, selected, "private", None)
    snapshot = objects["project/a/v/1/index.html"]
    second = _s3_deliver(bundle, selected, "private", None)
    assert first["version"] == 1 and second["version"] == 2
    assert first["url"] is None
    assert objects["project/a/v/1/index.html"] == snapshot
    assert json.loads(objects["project/index.json"])[0]["slug"] == "a"
    assert all(extra["ACL"] == "private" for key, extra in uploads)


def test_s3_index_never_lists_private_artifacts_publicly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Audit 2026-11-05: a private publish added its slug and title to the shared index,
    # and the next public publish rewrote that index as public-read
    import io

    from framework.publish_bundle import _s3_deliver

    objects: dict[str, tuple[bytes, str | None]] = {}

    class MissingError(Exception):
        def __init__(self) -> None:
            self.response = {"Error": {"Code": "NoSuchKey"}}

    class Client:
        def get_bucket_policy_status(self, **kwargs: Any) -> dict[str, Any]:
            return {"PolicyStatus": {"IsPublic": False}}

        def get_paginator(self, operation: str) -> Any:
            return self

        def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [{"Contents": [{"Key": k} for k in objects if k.startswith(kwargs["Prefix"])]}]

        def upload_file(self, path: str, bucket: str, key: str, **kwargs: Any) -> None:
            objects[key] = (Path(path).read_bytes(), kwargs["ExtraArgs"].get("ACL"))

        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            if kwargs["Key"] not in objects:
                raise MissingError()
            return {"Body": io.BytesIO(objects[kwargs["Key"]][0])}

        def put_object(self, **kwargs: Any) -> None:
            objects[kwargs["Key"]] = (kwargs["Body"], kwargs.get("ACL"))

    client = Client()
    bucket = SimpleNamespace(
        bucket="reports",
        client=lambda: client,
        key=lambda name: "shared/" + name,
        url=lambda key: f"https://reports.example/{key}",
    )
    monkeypatch.setattr("framework.storage.resolve_bucket", lambda name: bucket)
    public = Destination("results", "s3", {"bucket": "reports"})
    vault = Destination("vault", "s3", {"bucket": "reports", "visibility": "private"})

    def index() -> tuple[list[str], str | None]:
        body, acl = objects["shared/index.json"]
        return [e["slug"] for e in json.loads(body)], acl

    secret, table = tmp_path / "secret", tmp_path / "table"
    build_bundle(pd.DataFrame({"x": [1]}), secret, slug="secret-table", title="Secret")
    build_bundle(pd.DataFrame({"x": [1]}), table, slug="public-table")
    _s3_deliver(secret, vault, "private", None)
    assert index() == (["secret-table"], "private")
    _s3_deliver(table, public, None, None)
    assert index() == (["public-table"], "public-read")
    assert b"Secret" not in objects["shared/index.html"][0]
    # A public artifact on a private destination keeps that destination's index private
    _s3_deliver(table, vault, "public", None)
    assert index() == (["public-table"], "private")
    # An older index entry without a recorded visibility is not listed publicly
    objects["shared/index.json"] = (
        json.dumps([{"slug": "legacy", "title": "Old"}]).encode(),
        "private",
    )
    _s3_deliver(table, public, None, None)
    assert index() == (["public-table"], "public-read")


@pytest.mark.parametrize("layout", ["split", "flat"])
def test_project_settings_preserve_named_destinations(tmp_path: Path, layout: str) -> None:
    import yaml

    publish = {"default": "pages", "destinations": {"pages": {"driver": "github"}}}
    spec = fw.spec_resolve(
        [{"language": "python", "settings_layout": layout, "publish": publish}], name="Study"
    )
    assert spec.spec["publish"] == publish
    fw.project_create(spec.spec, tmp_path / "project", headless=True)
    settings = yaml.safe_load((tmp_path / "project/settings.yml").read_text())
    assert settings["default"]["publish"] == publish


@pytest.mark.parametrize("route", ["explicit", "default", "legacy"])
def test_cloud_uses_common_bundle_before_legacy_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    import framework.publish_bundle as bundles

    built: list[dict[str, Any]] = []
    real_build = bundles.build_bundle
    config = (
        {"publish": {"default": "hosted", "destinations": {"hosted": {"driver": "cloud"}}}}
        if route == "default"
        else {}
    )
    monkeypatch.setattr(
        importlib.import_module("framework.settings"), "settings_read", lambda: config
    )
    monkeypatch.setattr("framework.storage.settings_read", lambda: config)
    monkeypatch.setattr("framework.storage._has_project_token", lambda: True)

    def build(*args: Any, **kwargs: Any) -> dict[str, Any]:
        manifest = real_build(*args, **kwargs)
        built.append(manifest)
        return manifest

    def upload(path: Path, **kwargs: Any) -> str:
        assert built[0]["schema"] == "framework.bundle/v1"
        assert json.loads(path.read_text())["schema"] == "framework.artifact/v1"
        return "https://cloud.example/d/table"

    monkeypatch.setattr(bundles, "build_bundle", build)
    monkeypatch.setattr("framework.cloud.cloud_publish", upload)
    assert (
        fw.publish(pd.DataFrame({"value": [1.2345]}), to="cloud") == "https://cloud.example/d/table"
    )
    assert len(built) == 1


@pytest.mark.parametrize("status", [True, None, "denied"])
def test_private_s3_rejects_public_or_unverifiable_bucket_before_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: Any
) -> None:
    from framework.publish_bundle import _s3_deliver

    class Client:
        uploaded = False

        def get_bucket_policy_status(self, **kwargs: Any) -> dict[str, Any]:
            if status == "denied":
                raise RuntimeError("AccessDenied")
            return {"PolicyStatus": {"IsPublic": status}}

        def upload_file(self, *args: Any, **kwargs: Any) -> None:
            self.uploaded = True

    client = Client()
    monkeypatch.setattr(
        "framework.storage.resolve_bucket",
        lambda name: SimpleNamespace(bucket="hostile-public", client=lambda: client),
    )
    bundle = tmp_path / "bundle"
    build_bundle(pd.DataFrame({"x": [1]}), bundle)
    with pytest.raises(fw.FrameworkError, match="Nothing was uploaded"):
        _s3_deliver(
            bundle,
            Destination("archive", "s3", {"bucket": "results", "visibility": "private"}),
            "private",
            None,
        )
    assert not client.uploaded


def test_destination_private_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        importlib.import_module("framework.settings"),
        "settings_read",
        lambda: {
            "publish": {
                "destinations": {"lab": {"driver": "folder", "path": "/lab", "private": True}}
            }
        },
    )
    selected = destination("lab")
    assert selected is not None
    assert selected.options["visibility"] == "private"


def test_destination_credentials_require_environment_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    from framework.settings import settings_read

    path = tmp_path / "settings.yml"
    config = {
        "default": {
            "publish": {
                "destinations": {"results": {"driver": "s3", "access_key": "literal-secret"}}
            }
        }
    }
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(fw.SettingsError, match="environment reference"):
        settings_read(path)
    config["default"]["publish"]["destinations"]["results"]["access_key"] = 'env("RESULTS_KEY")'
    monkeypatch.setenv("RESULTS_KEY", "resolved-environment-value")
    path.write_text(yaml.safe_dump(config))
    assert (
        settings_read(path)["publish"]["destinations"]["results"]["access_key"]
        == "resolved-environment-value"
    )


def test_split_publishing_credentials_resolve_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from framework.settings import settings_read

    (tmp_path / "settings.yml").write_text("default:\n  publish: publishing.yml\n")
    (tmp_path / "publishing.yml").write_text(
        "publish:\n  destinations:\n    results:\n      driver: s3\n"
        '      access_key: env("RESULTS_ACCESS")\n'
    )
    monkeypatch.setenv("RESULTS_ACCESS", "resolved-environment-value")
    assert (
        settings_read(tmp_path / "settings.yml")["publish"]["destinations"]["results"]["access_key"]
        == "resolved-environment-value"
    )


def test_relative_folder_path_starts_at_the_project_root(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "settings.yml").write_text(
        "default:\n  project_name: Test\n  publish:\n    default: local\n    destinations:\n"
        "      local: {driver: folder, path: published, visibility: public}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("framework.cloud.project_token", lambda: None)
    notebooks = project / "notebooks"
    notebooks.mkdir()
    monkeypatch.chdir(notebooks)
    fw.publish(pd.DataFrame({"x": [1, 2]}), slug="from-notebook")
    assert (project / "published" / "from-notebook" / "artifact.json").is_file()
    assert not (notebooks / "published").exists()


def test_doc_title_reads_front_matter(tmp_path: Path) -> None:
    from framework.publish_bundle import doc_title

    qmd = tmp_path / "a.qmd"
    qmd.write_text('---\ntitle: "Blood pressure"\nformat: html\n---\n\nText\n', encoding="utf-8")
    assert doc_title(qmd) == "Blood pressure"
    notebook = tmp_path / "b.ipynb"
    notebook.write_text(
        json.dumps(
            {"cells": [{"cell_type": "raw", "source": ["---\n", "title: From raw\n", "---"]}]}
        ),
        encoding="utf-8",
    )
    assert doc_title(notebook) == "From raw"
    # Hand testing 2026-11-07: Framework's own ipynb stub opens with a "# Title" cell
    for source, title in (
        (["# Setup and settings\n", "\n", "Text"], "Setup and settings"),
        ("# Closed #\n", "Closed"),
        ("## Level two\n", None),
        ("Text first\n# Later", None),
    ):
        notebook.write_text(
            json.dumps({"cells": [{"cell_type": "markdown", "source": source}]}),
            encoding="utf-8",
        )
        assert doc_title(notebook) == title
    (tmp_path / "c.qmd").write_text("# No front matter\n", encoding="utf-8")
    assert doc_title(tmp_path / "c.qmd") is None
    assert doc_title(tmp_path / "missing.qmd") is None


def test_publish_notebook_to_a_folder_takes_the_yaml_title(
    project: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import subprocess

    from framework import _pages

    (project / "settings.yml").write_text(
        "default:\n  project_name: Test\n  publish:\n    default: local\n    destinations:\n"
        "      local: {driver: folder, path: published, visibility: public}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("framework.cloud.project_token", lambda: None)

    def render(command: list[str], **kwargs: Any) -> Any:
        out = Path(command[command.index("--output-dir") + 1])
        (out / "report.html").write_text("<p>rendered</p>", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(_pages.shutil, "which", lambda name: "/usr/bin/quarto")
    monkeypatch.setattr(_pages.subprocess, "run", render)
    notebook = project / "report.qmd"
    notebook.write_text("---\ntitle: Weekly report\n---\n", encoding="utf-8")
    caplog.set_level("INFO", logger="framework")
    location = fw.publish_notebook(notebook)
    manifest = json.loads((project / "published/report/artifact.json").read_text())
    assert manifest["title"] == "Weekly report"
    assert "Published version 1:" in caplog.text and location in caplog.text
    fw.publish_notebook(notebook, title="Override", description="Short")
    manifest = json.loads((project / "published/report/artifact.json").read_text())
    assert (manifest["title"], manifest["description"]) == ("Override", "Short")


def test_site_root_layout_keeps_only_the_latest_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ports 2026-11-07: a deck published to docs/ became docs/<slug>/v/1/ plus an index,
    # four copies of 7.8 MB, where `cp deck.html docs/index.html` was wanted
    site = tmp_path / "docs"
    config = {
        "publish": {
            "destinations": {
                "pages": {
                    "driver": "folder",
                    "path": str(site),
                    "url": "https://me.github.io/deck",
                    "layout": "site-root",
                }
            }
        }
    }
    monkeypatch.setattr(
        importlib.import_module("framework.settings"), "settings_read", lambda: config
    )
    monkeypatch.setattr("framework.storage.settings_read", lambda: config)
    monkeypatch.setattr("framework.cloud.project_token", lambda: None)
    site.mkdir()
    (site / "CNAME").write_text("deck.example.org", encoding="utf-8")
    deck = tmp_path / "deck.html"
    deck.write_text("<html><body>Slides</body></html>", encoding="utf-8")

    assert fw.publish(deck, to="pages") == "https://me.github.io/deck/"
    assert sorted(p.name for p in site.iterdir()) == [".framework-site.json", "CNAME", "index.html"]
    assert (site / "index.html").read_text(encoding="utf-8") == deck.read_text(encoding="utf-8")
    record = json.loads((site / ".framework-site.json").read_text(encoding="utf-8"))
    assert (record["version"], record["slug"], record["files"]) == (1, "deck", ["index.html"])

    # A table replaces the deck: the deck's files go, the folder's own files stay
    fw.publish(pd.DataFrame({"n": [1]}), to="pages", slug="counts")
    names = sorted(p.name for p in site.iterdir())
    assert "CNAME" in names and "data.csv" in names and "v" not in names
    # Versions count per slug (N7): "counts" starts at 1, not the deck's 2
    assert json.loads((site / ".framework-site.json").read_text())["version"] == 1
    fw.publish(pd.DataFrame({"n": [2]}), to="pages", slug="counts")
    assert json.loads((site / ".framework-site.json").read_text())["version"] == 2
    listed = fw.publish_list(to="pages")
    assert listed["key"].tolist() == ["counts/"]
    assert listed["url"].tolist() == ["https://me.github.io/deck/"]
    fw.publish(deck, to="pages")
    assert sorted(p.name for p in site.iterdir()) == [".framework-site.json", "CNAME", "index.html"]
    assert json.loads((site / ".framework-site.json").read_text())["version"] == 1


def test_site_root_publishes_only_rendered_output(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ports 2026-11-07 (N6): the deck's .qmd source was written beside index.html
    import subprocess

    from framework import _pages

    (project / "settings.yml").write_text(
        "default:\n  project_name: Test\n  publish:\n    default: pages\n"
        "    destinations:\n"
        "      pages: {driver: folder, path: docs, visibility: public, layout: site-root}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("framework.cloud.project_token", lambda: None)

    def render(command: list[str], **kwargs: Any) -> Any:
        out = Path(command[command.index("--output-dir") + 1])
        (out / "deck.html").write_text("<p>slides</p>", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(_pages.shutil, "which", lambda name: "/usr/bin/quarto")
    monkeypatch.setattr(_pages.subprocess, "run", render)
    for name in ("deck.qmd", "deck.ipynb"):
        notebook = project / name
        notebook.write_text(
            "---\ntitle: Deck\n---\n" if name.endswith(".qmd") else '{"cells": []}',
            encoding="utf-8",
        )
        fw.publish_notebook(notebook, to="pages")
        site = project / "docs"
        assert sorted(p.name for p in site.iterdir()) == [".framework-site.json", "index.html"]
        assert "<p>slides</p>" in (site / "index.html").read_text(encoding="utf-8")


def test_layout_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    from framework.publish_bundle import Destination, validate

    with pytest.raises(fw.FrameworkError, match="versioned or site-root"):
        validate(Destination("x", "folder", {"path": "x", "layout": "flat"}), None, None, None)
    with pytest.raises(fw.FrameworkError, match="folder destinations only"):
        validate(Destination("x", "s3", {"layout": "site-root"}), None, None, None)
