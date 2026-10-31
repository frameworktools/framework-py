from __future__ import annotations

import gzip
import os
import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import framework as fw
from framework import storage


class FakeS3:
    """Records uploads like boto3's S3 client."""

    def __init__(self, acl_supported: bool = True) -> None:
        self.uploads: dict[str, dict[str, Any]] = {}
        self.acl_supported = acl_supported

    def upload_file(self, path: str, bucket: str, key: str, ExtraArgs: dict[str, Any]) -> None:  # noqa: N803
        if "ACL" in ExtraArgs and not self.acl_supported:
            raise RuntimeError("An error occurred (AccessControlListNotSupported)")
        self.uploads[key] = {"bucket": bucket, "body": Path(path).read_bytes(), **ExtraArgs}

    def list_objects_v2(self, Bucket: str, Prefix: str, MaxKeys: int) -> dict[str, Any]:  # noqa: N803
        keys = [k for k in self.uploads if k.startswith(Prefix)][:MaxKeys]
        return {"Contents": [{"Key": k, "Size": len(self.uploads[k]["body"])} for k in keys]}

    def head_bucket(self, Bucket: str) -> None:  # noqa: N803
        if Bucket != "my-reports":
            raise RuntimeError("404")


@pytest.fixture
def s3(project: Path, monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    (project / "settings.yml").write_text(
        "default:\n"
        "  connections:\n"
        "    default_storage_bucket: reports\n"
        "    storage_buckets:\n"
        "      reports: {bucket: my-reports, region: us-east-2, prefix: project-x}\n"
        "      minio: {bucket: local, endpoint: 'http://localhost:9000', static_hosting: true}\n",
        encoding="utf-8",
    )
    os.environ.update(S3_ACCESS_KEY="key", S3_SECRET_KEY="secret")
    fake = FakeS3()
    monkeypatch.setattr(storage.Bucket, "client", lambda self: fake)
    return fake


def test_publish_file_and_folder(project: Path, s3: FakeS3) -> None:
    (project / "outputs/public").mkdir(parents=True)
    report = project / "outputs/public/report.html"
    report.write_text("<p>hi</p>", encoding="utf-8")
    url = fw.publish(report)
    assert url == "https://my-reports.s3.us-east-2.amazonaws.com/project-x/report.html"
    upload = s3.uploads["project-x/report.html"]
    assert (upload["ContentType"], upload["ACL"]) == ("text/html", "public-read")

    (project / "outputs/public/charts").mkdir()
    (project / "outputs/public/charts/a.png").write_bytes(b"png")
    urls = fw.publish(project / "outputs/public/charts", dest="reports/charts")
    assert urls == ["https://my-reports.s3.us-east-2.amazonaws.com/project-x/reports/charts/a.png"]


def test_flagged_paths_need_confirmation_without_a_terminal(project: Path, s3: FakeS3) -> None:
    secret = project / "outputs/private/ids.csv"
    secret.parent.mkdir(parents=True)
    secret.write_text("id\n1\n", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish(secret)
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish_dir(secret.parent)
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish_data(secret, "ids.csv")
    assert s3.uploads == {}


def test_buckets_without_acls(project: Path, s3: FakeS3) -> None:
    s3.acl_supported = False
    (project / "a.txt").write_text("a", encoding="utf-8")
    fw.publish(project / "a.txt")
    assert "ACL" not in s3.uploads["project-x/a.txt"]


def test_custom_endpoint_urls(project: Path, s3: FakeS3) -> None:
    (project / "a.txt").write_text("a", encoding="utf-8")
    assert fw.publish(project / "a.txt", connection="minio") == "http://localhost:9000/local/a.txt"


def test_publish_data(project: Path, s3: FakeS3) -> None:
    frame = pd.DataFrame({"x": [1, 2]})
    url = fw.publish_data(frame, "data/x.csv", compress=True)
    assert url.endswith("project-x/data/x.csv.gz")
    assert gzip.decompress(s3.uploads["project-x/data/x.csv.gz"]["body"]) == b"x\n1\n2\n"
    fw.publish_data(frame, "data/x.parquet", format="parquet")
    with pytest.raises(fw.FrameworkError, match="rds is R-only"):
        fw.publish_data(frame, "x.rds", format="rds")


def test_publish_dir_pattern_and_list(project: Path, s3: FakeS3) -> None:
    folder = project / "site"
    (folder / "sub").mkdir(parents=True)
    (folder / "index.html").write_text("i", encoding="utf-8")
    (folder / "sub/style.css").write_text("c", encoding="utf-8")
    (folder / "notes.txt").write_text("n", encoding="utf-8")
    urls = fw.publish_dir(folder, pattern=r"\.(html|css)$")
    assert len(urls) == 2
    listed = fw.publish_list()
    assert sorted(listed["key"]) == ["project-x/site/index.html", "project-x/site/sub/style.css"]
    assert fw.publish_list(prefix="site/sub")["key"].tolist() == ["project-x/site/sub/style.css"]


def test_storage_test(project: Path, s3: FakeS3) -> None:
    assert fw.storage_test() is True
    assert fw.storage_test("minio") is False


def test_bucket_errors(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(fw.FrameworkError, match="No storage buckets configured"):
        fw.storage_test()
    (project / "settings.yml").write_text(
        "default:\n  connections: {storage_buckets: {r: {bucket: b}}}\n", encoding="utf-8"
    )
    with pytest.raises(fw.FrameworkError, match="S3 credentials not found"):
        fw.storage_test()
    os.environ.update(S3_ACCESS_KEY="k", S3_SECRET_KEY="s")
    with pytest.raises(fw.FrameworkError, match="'other' not found"):
        fw.storage_test("other")


def test_nowhere_to_publish(project: Path) -> None:
    (project / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="Nowhere to publish"):
        fw.publish(project / "a.txt")
    (project / ".env").write_text("FW_PROJECT_TOKEN=fwp_abc\n", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="Framework Cloud publishing takes rendered HTML"):
        fw.publish(project / "a.txt")


@pytest.mark.skipif(shutil.which("quarto") is None, reason="quarto is not installed")
def test_publish_notebook(project: Path, s3: FakeS3) -> None:
    notebook = project / "analysis.qmd"
    notebook.write_text("---\ntitle: A\n---\n\nHello.\n", encoding="utf-8")
    assert fw.publish_notebook(notebook).endswith("project-x/analysis.html")
    assert fw.publish_notebook(notebook, connection="minio") == (
        "http://localhost:9000/local/analysis/"
    )
    assert "analysis/index.html" in s3.uploads


def test_override_with_include_private_or_allow_private(
    project: Path, s3: FakeS3, tmp_path_factory: pytest.TempPathFactory
) -> None:
    secret = project / "inputs/Private/summary.csv"
    secret.parent.mkdir(parents=True)
    secret.write_text("n\n10\n", encoding="utf-8")
    assert fw.publish(secret, include_private=True).endswith("project-x/summary.csv")
    with pytest.warns(DeprecationWarning, match="use include_private"):
        assert fw.publish_data(secret, "s.csv", allow_private=True).endswith("project-x/s.csv")

    # Outside a project too: the rule looks at the whole path
    elsewhere = tmp_path_factory.mktemp("outside") / "private"
    elsewhere.mkdir()
    (elsewhere / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="include_private=True"):
        fw.publish_dir(elsewhere, dest="d")
    assert fw.publish_dir(elsewhere, dest="d", include_private=True)
