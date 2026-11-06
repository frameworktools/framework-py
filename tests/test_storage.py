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


def test_nowhere_to_publish(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "a.txt").write_text("a", encoding="utf-8")
    with pytest.raises(fw.FrameworkError, match="Nowhere to publish"):
        fw.publish(project / "a.txt")
    (project / ".env").write_text("FW_PROJECT_TOKEN=fwp_abc\n", encoding="utf-8")
    monkeypatch.setattr("framework.cloud.cloud_publish", lambda *a, **k: "https://x/d/text")
    assert fw.publish(project / "a.txt") == "https://x/d/text"


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


def test_explicit_destinations(project: Path, s3: FakeS3, monkeypatch: pytest.MonkeyPatch) -> None:
    file = project / "plot.png"
    file.write_bytes(b"plot")
    monkeypatch.setattr("framework.cloud.cloud_publish", lambda *a, **k: "https://x/d/plot")
    assert fw.publish(file, to="cloud") == "https://x/d/plot"
    assert fw.publish(file, to="bucket").endswith("plot.png")
    with pytest.raises(fw.FrameworkError, match="connection"):
        fw.publish(file, to="cloud", connection="minio")
    with pytest.raises(fw.FrameworkError, match="to must"):
        fw.publish(file, to="wrong")


def test_object_exports(tmp_path: Path) -> None:
    table = storage._artifact_file(pd.DataFrame({"estimate": [1.5]}), tmp_path)
    assert table.name == "table.csv"
    assert table.read_text() == "estimate\n1.5\n"
    matplotlib = pytest.importorskip("matplotlib.figure")
    figure = matplotlib.Figure()
    figure.subplots().plot([1, 2], [3, 4])
    path = storage._artifact_file(figure, tmp_path)
    assert path.read_bytes().startswith(b"\x89PNG")
    with pytest.raises(fw.FrameworkError, match="accepts"):
        storage._artifact_file(object(), tmp_path)


def test_structured_table_types_and_missing_values(tmp_path: Path) -> None:
    import json
    from datetime import date

    frame = pd.DataFrame(
        {
            "estimate": pd.Series([1.5, pd.NA], dtype="Float64"),
            "included": pd.Series([True, pd.NA], dtype="boolean"),
            "count": pd.Series([2, pd.NA], dtype="Int64"),
            "label": ["<estimate>", None],
            "date": [date(2026, 1, 2), None],
            "time": pd.to_datetime(["2026-01-02T03:04:05Z", None]),
        }
    )
    path = storage._artifact_file(frame, tmp_path, cloud=True)
    document = json.loads(path.read_text())
    assert path.name == "table.json"
    assert document["schema"] == "framework.artifact/v1"
    assert document["source"]["language"] == "python"
    table = document["tables"][0]
    assert [c["type"] for c in table["columns"]] == [
        "number",
        "boolean",
        "number",
        "string",
        "date",
        "datetime",
    ]
    assert table["rows"] == [
        [1.5, True, 2, "<estimate>", "2026-01-02", "2026-01-02T03:04:05+00:00"],
        [None] * 6,
    ]
    assert table["row_count"] == 2
    # A default RangeIndex is not exported; a meaningful one becomes the first column.
    assert table["columns"][0]["name"] == "estimate"
    frame.index = pd.Index(["row-a", "row-b"], name="id")
    labeled = json.loads(storage._artifact_file(frame, tmp_path, cloud=True).read_text())
    assert labeled["tables"][0]["columns"][0]["name"] == "id"
    assert [row[0] for row in labeled["tables"][0]["rows"]] == ["row-a", "row-b"]


def test_structured_statsmodels_results(tmp_path: Path) -> None:
    import json

    sm = pytest.importorskip("statsmodels.api")
    x = sm.add_constant([0, 1, 2, 3, 4, 5, 6, 7])
    for fit in [
        sm.OLS([1, 2, 2, 4, 4, 5, 6, 6], x).fit(),
        sm.GLM([0, 0, 1, 0, 1, 1, 0, 1], x, family=sm.families.Binomial()).fit(),
    ]:
        path = storage._artifact_file(fit, tmp_path, cloud=True)
        document = json.loads(path.read_text())
        assert path.name == "model.json"
        assert document["kind"] == "model"
        coefficients = document["tables"][0]
        assert coefficients["row_count"] == 2
        assert coefficients["rows"][1][1] == pytest.approx(fit.params[1])
        assert coefficients["rows"][1][4] == pytest.approx(fit.pvalues[1])
        assert {m["name"]: m["value"] for m in document["metrics"]}["nobs"] == 8
        assert "endog" not in document and "resid" not in document


def test_seaborn_and_axes_exports(tmp_path: Path) -> None:
    sns = pytest.importorskip("seaborn")
    import matplotlib.pyplot as plt

    frame = pd.DataFrame({"x": [1, 2, 3], "y": [2, 1, 4]})
    axes = sns.scatterplot(data=frame, x="x", y="y")
    grid = sns.relplot(data=frame, x="x", y="y")
    try:
        for value in [axes, grid]:
            path = storage._artifact_file(value, tmp_path, cloud=True)
            assert path.name == "figure.svg"
            assert b"<svg" in path.read_bytes()[:2000]
    finally:
        plt.close("all")


def test_large_integer_identifiers_remain_exact(tmp_path: Path) -> None:
    import json

    frame = pd.DataFrame(
        {
            "id": pd.Series([2**63 - 1, pd.NA], dtype="Int64"),
            "ordinary": pd.Series([42, pd.NA], dtype="Int64"),
        }
    )
    document = json.loads(storage._artifact_file(frame, tmp_path, cloud=True).read_text())
    table = document["tables"][0]
    assert [(c["name"], c["type"]) for c in table["columns"]] == [
        ("id", "string"),
        ("ordinary", "number"),
    ]
    assert table["rows"] == [["9223372036854775807", 42], [None, None]]


def test_formula_model_keeps_formula_without_training_rows(tmp_path: Path) -> None:
    import json

    smf = pytest.importorskip("statsmodels.formula.api")
    frame = pd.DataFrame(
        {"response": [1, 2, 2, 4, 4, 5, 6, 6], "predictor": [0, 1, 2, 3, 4, 5, 6, 7]}
    )
    frame.index = [f"private-observation-{i}" for i in range(len(frame))]
    model = smf.ols("response ~ predictor", data=frame).fit()
    path = storage._artifact_file(model, tmp_path, cloud=True)
    document = json.loads(path.read_text())
    assert document["formula"] == "response ~ predictor"
    assert "private-observation" not in path.read_text()
    assert document["tables"][0]["row_count"] == 2


def test_statsmodels_interval_method_metadata(tmp_path: Path) -> None:
    import json

    sm = pytest.importorskip("statsmodels.api")
    x = sm.add_constant([0, 1, 2, 3, 4, 5, 6, 7])
    for use_t in (True, False):
        model = sm.OLS([1, 2, 2, 4, 4, 5, 6, 6], x).fit(use_t=use_t)
        document = json.loads(storage._artifact_file(model, tmp_path, cloud=True).read_text())
        metrics = {m["name"]: m["value"] for m in document["metrics"]}
        assert metrics["confidence_level"] == 0.95
        assert metrics["interval_method"] == ("wald-t" if use_t else "wald-normal")
        assert document["tables"][0]["rows"][1][5:] == pytest.approx(model.conf_int()[1])
        assert set(document) == {"schema", "kind", "source", "model", "tables", "metrics"}


def test_legacy_bucket_tracks_metadata_without_paths_or_content(
    project: Path, s3: FakeS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import json
    from types import SimpleNamespace

    source = project / "report.txt"
    source.write_text("artifact-content-sentinel", encoding="utf-8")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("framework.cloud.project_token", lambda: "project-token")
    monkeypatch.setattr(
        "framework.cloud.cloud_server", lambda **kw: SimpleNamespace(url="https://cloud")
    )

    def api(path: str, token: str, server: str, **kwargs: Any) -> dict[str, Any]:
        if path == "/api/v1/me":
            return {"project": "study"}
        assert path == "/api/v2/projects/study/artifacts/external"
        calls.append(kwargs["body"])
        if len(calls) > 1:
            raise RuntimeError("409 Conflict: version already exists")
        return {}

    monkeypatch.setattr("framework.cloud.api", api)
    result = fw.publish(source, to="bucket")
    assert calls[0]["driver"] == "s3"
    assert calls[0]["version"] == 1
    assert calls[0]["url"] == result
    assert calls[0]["version_url"] is None
    assert calls[0]["size_bytes"] == source.stat().st_size
    assert calls[0]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert str(project) not in json.dumps(calls)
    assert "artifact-content-sentinel" not in json.dumps(calls)
    assert not {"location", "path", "files", "content", "representation"} & calls[0].keys()
    source.write_text("replacement", encoding="utf-8")
    with pytest.warns(fw.FrameworkWarning, match="409 Conflict"):
        assert fw.publish(source, to="bucket") == result
    assert s3.uploads["project-x/report.txt"]["body"] == b"replacement"


# Audit 2026-11-05 regressions ------------------------------------------------------------


def _render_into_subfolder(monkeypatch: pytest.MonkeyPatch, *modules: Any) -> None:
    """Fake quarto render as a project's _quarto.yml does: the notebook's folder is kept."""
    import subprocess

    def render(command: list[str], **kwargs: Any) -> Any:
        out = Path(command[command.index("--output-dir") + 1])
        page = out / "notebooks" / f"{Path(command[2]).stem}.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("<p>rendered</p>", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    for module in modules:
        monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/quarto")
        monkeypatch.setattr(module.subprocess, "run", render)


def test_publish_notebook_finds_output_in_a_subfolder(
    project: Path, s3: FakeS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every new() project's _quarto.yml sent output to <temp>/notebooks/, and publishing
    # looked only at the top: "No HTML output found"
    _render_into_subfolder(monkeypatch, storage)
    notebook = project / "notebooks" / "report.qmd"
    notebook.parent.mkdir()
    notebook.write_text("---\ntitle: R\n---\n", encoding="utf-8")
    assert fw.publish_notebook(notebook).endswith("project-x/report.html")
    assert s3.uploads["project-x/report.html"]["body"] == b"<p>rendered</p>"
    fw.publish_notebook(notebook, connection="minio", self_contained=False)
    assert s3.uploads["report/index.html"]["body"] == b"<p>rendered</p>"


def test_cloud_publish_finds_output_in_a_subfolder(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from framework import cloud

    _render_into_subfolder(monkeypatch, cloud)
    sent: list[bytes] = []
    (project / ".env").write_text(
        "FW_PROJECT_TOKEN=fwp_project0123\nFW_CLOUD_URL=https://cloud.example.edu\n",
        encoding="utf-8",
    )
    import httpx

    def fake(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(201, json={"url": "https://cloud.example.edu/d/r", "version": 1})

    monkeypatch.setattr(cloud, "_transport", httpx.MockTransport(fake))
    notebook = project / "notebooks" / "report.qmd"
    notebook.parent.mkdir()
    notebook.write_text("---\ntitle: R\n---\n", encoding="utf-8")
    assert cloud.cloud_publish(notebook) == "https://cloud.example.edu/d/r"
    assert b"<p>rendered</p>" in sent[0]
    assert cloud.cloud_publish(notebook, self_contained=False) == "https://cloud.example.edu/d/r"
    assert b'name="entrypoint"\r\n\r\nreport.html' in sent[1]


def test_bundle_destinations_find_output_in_a_subfolder(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Folder, GitHub, named S3, and cloud bundles failed: "No single HTML page found"
    from framework import _pages

    _render_into_subfolder(monkeypatch, _pages)
    site = project / "site"
    (project / "settings.yml").write_text(
        "default:\n  publish:\n    destinations:\n"
        f"      lab: {{driver: folder, path: '{site.as_posix()}'}}\n",
        encoding="utf-8",
    )
    notebook = project / "notebooks" / "report.qmd"
    notebook.parent.mkdir()
    notebook.write_text("---\ntitle: R\n---\n", encoding="utf-8")
    fw.publish(notebook, to="lab", slug="report")
    assert (site / "report" / "index.html").read_text(encoding="utf-8") == "<p>rendered</p>"


@pytest.mark.skipif(shutil.which("quarto") is None, reason="quarto is not installed")
def test_publish_notebook_in_a_project_with_quarto_settings(project: Path, s3: FakeS3) -> None:
    (project / "_quarto.yml").write_text(
        "project:\n  type: default\n  output-dir: outputs\n", encoding="utf-8"
    )
    notebook = project / "notebooks" / "report.qmd"
    notebook.parent.mkdir()
    notebook.write_text("---\ntitle: R\n---\n\nHello.\n", encoding="utf-8")
    assert fw.publish_notebook(notebook).endswith("project-x/report.html")
    assert b"Hello." in s3.uploads["project-x/report.html"]["body"]


def test_s3_credentials_come_from_the_project_env(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # settings_read() no longer leaves .env values in os.environ, so the bucket reads them
    (project / "settings.yml").write_text(
        "default:\n  connections:\n    storage_buckets:\n      reports: {bucket: b}\n",
        encoding="utf-8",
    )
    (project / ".env").write_text("S3_ACCESS_KEY=from-file\nS3_SECRET_KEY=s\n", encoding="utf-8")
    os.environ.pop("S3_ACCESS_KEY", None)
    os.environ["S3_SECRET_KEY"] = "shell"
    bucket = storage.resolve_bucket("reports")
    assert (bucket.access_key, bucket.secret_key) == ("from-file", "shell")
    assert "S3_ACCESS_KEY" not in os.environ


def test_publish_dir_pattern_ignores_the_folders_location(project: Path, s3: FakeS3) -> None:
    # Audit 2026-11-05: the pattern matched the absolute path, so a parent folder's name
    # matched every file; a bad pattern raised re.PatternError
    folder = project / "site-figures" / "out"
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("a", encoding="utf-8")
    (folder / "figures").mkdir()
    (folder / "figures" / "b.png").write_bytes(b"png")
    urls = fw.publish_dir(folder, dest="d", pattern="figures")
    assert [url.rsplit("/", 2)[-2:] for url in urls] == [["figures", "b.png"]]
    with pytest.raises(fw.FrameworkError, match="Invalid pattern"):
        fw.publish_dir(folder, dest="d", pattern="(")


def test_connection_with_a_destination_bucket_is_refused(
    project: Path, s3: FakeS3, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Audit 2026-11-05: connection= was silently ignored with a named S3 destination
    (project / "settings.yml").write_text(
        (project / "settings.yml").read_text(encoding="utf-8")
        + "  publish:\n    destinations:\n      results: {driver: s3, bucket: reports}\n",
        encoding="utf-8",
    )
    path = project / "a.png"
    path.write_bytes(b"png")
    with pytest.raises(fw.FrameworkError, match="sets its own bucket"):
        fw.publish(path, to="results", connection="minio")
