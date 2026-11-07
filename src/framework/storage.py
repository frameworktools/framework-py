"""S3-compatible storage buckets and publishing.

Buckets live under ``connections.storage_buckets`` in the settings::

    connections:
      default_storage_bucket: reports
      storage_buckets:
        reports: {bucket: my-reports, region: us-east-1, prefix: project-x}

Credentials come from the environment (``S3_ACCESS_KEY`` and
``S3_SECRET_KEY``, or the AWS variables), usually set in ``.env``. As in R,
environment variables take precedence over values in the settings.

Publishing uploads files with a public-read ACL and returns their URLs.
A source with "private" anywhere in its full path is listed with a warning
and needs confirmation: an interactive session asks, and a session with no
terminal raises an error unless ``include_private=True`` is passed.
"""

from __future__ import annotations

import gzip
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import pandas as pd

from framework._log import inform
from framework._paths import rendered_page
from framework._private import confirm_private_upload, include_private_arg
from framework.errors import FrameworkError
from framework.publish_bundle import doc_title
from framework.settings import settings_read

_CONTENT_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".json": "application/json",
    ".xml": "application/xml",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".parquet": "application/vnd.apache.parquet",
}


def _section(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    return value if isinstance(value, dict) else {}


def bucket_configs(config: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Every storage bucket in the settings, by name.

    Read from ``connections.storage_buckets``, and from a top-level
    ``storage_buckets`` section, where projects created by R before 2.0 have it.
    """
    config = config if config is not None else settings_read()
    found: dict[str, dict[str, Any]] = {}
    for source in (
        _section(config, "storage_buckets"),
        _section(_section(config, "connections"), "storage_buckets"),
    ):
        found.update(
            {name: dict(entry) for name, entry in source.items() if isinstance(entry, dict)}
        )
    return found


def default_bucket(config: Mapping[str, Any] | None = None) -> str | None:
    config = config if config is not None else settings_read()
    value = _section(config, "connections").get("default_storage_bucket") or config.get(
        "default_storage_bucket"
    )
    return str(value) if value else None


@dataclass(frozen=True)
class Bucket:
    """A resolved storage bucket: where uploads go and how to reach it."""

    name: str
    bucket: str
    region: str
    prefix: str = ""
    endpoint: str | None = None
    use_https: bool = True
    static_hosting: bool = False
    access_key: str = ""
    secret_key: str = ""
    session_token: str | None = None

    def key(self, dest: str) -> str:
        key = f"{self.prefix}/{dest}" if self.prefix else dest
        return re.sub(r"/+", "/", key).lstrip("/")

    def url(self, key: str) -> str:
        """The public URL of an object."""
        if self.endpoint:
            protocol = "https" if self.use_https else "http"
            return f"{protocol}://{self.endpoint}/{self.bucket}/{key}"
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{key}"

    def client(self) -> Any:
        try:
            import boto3
        except ImportError as error:
            raise FrameworkError(
                "Storage buckets need the boto3 package. "
                'Install it with: pip install "framework-py[s3]"'
            ) from error
        endpoint = (
            f"{'https' if self.use_https else 'http'}://{self.endpoint}" if self.endpoint else None
        )
        return boto3.client(
            "s3",
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            aws_session_token=self.session_token,
            region_name=self.region,
            endpoint_url=endpoint,
        )


def _first(
    config: Mapping[str, Any], keys: tuple[str, ...], env: tuple[str, ...], default: str = ""
) -> str:
    for name in env:
        if os.environ.get(name):
            return os.environ[name]
    # Then the project's .env, which settings_read() no longer leaves in os.environ
    from framework.settings import project_dotenv

    project_env = project_dotenv()
    for name in env:
        if project_env.get(name):
            return project_env[name]
    for key in keys:
        if config.get(key):
            return str(config[key])
    return default


def _build(name: str, entry: Mapping[str, Any]) -> Bucket:
    access = _first(entry, ("access_key_id", "access_key"), ("S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID"))
    secret = _first(
        entry, ("secret_access_key", "secret_key"), ("S3_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")
    )
    if not access or not secret:
        raise FrameworkError(
            "S3 credentials not found. Provide them in the connection settings or set\n"
            "  S3_ACCESS_KEY and S3_SECRET_KEY (or AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)"
        )
    bucket = _first(entry, ("bucket",), ("S3_BUCKET",))
    if not bucket:
        raise FrameworkError(
            "S3 bucket not specified. Set it in the connection settings or S3_BUCKET"
        )
    endpoint = _first(entry, ("endpoint",), ("S3_ENDPOINT",))
    return Bucket(
        name=name,
        bucket=bucket,
        region=_first(
            entry, ("region",), ("S3_REGION", "AWS_REGION", "AWS_DEFAULT_REGION"), "us-east-1"
        ),
        prefix=str(entry.get("prefix") or ""),
        endpoint=re.sub(r"^https?://", "", endpoint).rstrip("/") or None,
        use_https=not endpoint.startswith("http://"),
        static_hosting=entry.get("static_hosting") is True,
        access_key=access,
        secret_key=secret,
        session_token=_first(entry, ("session_token",), ("S3_SESSION_TOKEN", "AWS_SESSION_TOKEN"))
        or None,
    )


def resolve_bucket(connection: str | None = None) -> Bucket:
    """The named bucket, else the default, else one marked ``default: true``, else the first."""
    config = settings_read()
    buckets = bucket_configs(config)
    if not buckets:
        raise FrameworkError(
            "No storage buckets configured. Add one in settings/connections.yml:\n\n"
            "connections:\n  storage_buckets:\n    my_s3:\n      bucket: my-bucket\n"
            "      region: us-east-1\n  default_storage_bucket: my_s3"
        )
    if connection is not None:
        if connection not in buckets:
            raise FrameworkError(
                f"Storage bucket '{connection}' not found. Available: {', '.join(buckets)}"
            )
        return _build(connection, buckets[connection])
    name = default_bucket(config)
    if name not in buckets:
        name = next(
            (n for n, e in buckets.items() if e.get("default") is True), next(iter(buckets))
        )
    return _build(name, buckets[name])


# Uploading --------------------------------------------------------------------------


def _content_type(path: Path) -> str:
    return (
        _CONTENT_TYPES.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or ("application/octet-stream")
    )


def upload_file(path: Path, dest: str, bucket: Bucket) -> str:
    """Upload one file with a public-read ACL and return its URL."""
    if not path.is_file():
        raise FrameworkError(f"File not found: {path}")
    key = bucket.key(dest)
    client = bucket.client()
    extra = {"ContentType": _content_type(path), "ACL": "public-read"}
    try:
        client.upload_file(str(path), bucket.bucket, key, ExtraArgs=extra)
    except Exception as error:
        # Buckets with ACLs turned off reject the ACL; their policy decides access.
        if "AccessControlListNotSupported" not in str(error):
            raise FrameworkError(f"Failed to upload to S3: {error}") from error
        del extra["ACL"]
        try:
            client.upload_file(str(path), bucket.bucket, key, ExtraArgs=extra)
        except Exception as retry_error:
            raise FrameworkError(f"Failed to upload to S3: {retry_error}") from retry_error
    return bucket.url(key)


def _files(folder: Path, recursive: bool, pattern: str | None) -> list[Path]:
    found = folder.rglob("*") if recursive else folder.iterdir()
    files = sorted(p for p in found if p.is_file())
    if pattern is not None:
        # Matched against the path inside the folder, so the folder's own location and its
        # parents' names never match
        try:
            expression = re.compile(pattern)
        except re.error as error:
            raise FrameworkError(f"Invalid pattern {pattern!r}: {error}") from error
        files = [p for p in files if expression.search(p.relative_to(folder).as_posix())]
    return files


def _upload_folder(folder: Path, dest: str, bucket: Bucket, files: list[Path]) -> list[str]:
    return [upload_file(p, f"{dest}/{p.relative_to(folder).as_posix()}", bucket) for p in files]


def _has_project_token() -> bool:
    from framework.dotenv import value
    from framework.project import find_settings_file

    if os.environ.get("FW_PROJECT_TOKEN"):
        return True
    settings_file = find_settings_file()
    return bool(settings_file and value(settings_file.parent / ".env", "FW_PROJECT_TOKEN"))


def _target(connection: str | None, to: str | None = None) -> str:
    if to == "cloud":
        if connection is not None:
            raise FrameworkError('connection cannot be used with to="cloud".')
        return "cloud"
    if to == "bucket":
        return "s3"
    if to is not None:
        raise FrameworkError('to must be "cloud", "bucket", "github", or None.')
    if connection is not None or bucket_configs():
        return "s3"
    if _has_project_token():
        return "cloud"
    raise FrameworkError(
        "Nowhere to publish. Add a storage bucket in settings/connections.yml, or set up this "
        "project from Framework Cloud.\nA storage bucket looks like this:\n"
        "connections:\n  storage_buckets:\n    my_s3:\n      bucket: my-bucket\n"
        "      region: us-east-1\n  default_storage_bucket: my_s3"
    )


def _publish_github(
    to: str,
    source: Path,
    dest: str | None,
    connection: str | None,
    slug: str | bool | None,
    self_contained: bool,
) -> str:
    """``to="github"``, after rejecting arguments that mean something only elsewhere."""
    from framework._pages import github_publish

    if to != "github":
        raise FrameworkError(f'to must be "github" or None (got "{to}").')
    given = [
        name
        for name, used in (
            ("dest", dest is not None),
            ("connection", connection is not None),
            ("slug=True/False", isinstance(slug, bool)),
        )
        if used
    ]
    if given:
        raise FrameworkError(
            f'Not used with to="github": {", ".join(given)}. Name the published folder with '
            'slug="...".'
        )
    assert not isinstance(slug, bool)
    return github_publish(source, slug=slug, self_contained=self_contained)


def _slug_ignored(slug: str | bool | None) -> None:
    if slug is not None:
        import warnings

        from framework.errors import FrameworkWarning

        warnings.warn(
            "`slug` applies to Framework Cloud only; ignored for storage buckets.",
            FrameworkWarning,
            stacklevel=3,
        )


_ACCEPTED = (
    "publish() accepts a file or rendered folder; a pandas or polars DataFrame or Series; a "
    "numpy array; a pandas Styler; a scipy.stats test result; a statsmodels results object; "
    "a fitted scikit-learn, xgboost, or lightgbm model; a matplotlib Figure or Axes or a "
    "seaborn plot; a plotly Figure; or an altair chart."
)


def _artifact_file(source: Any, directory: Path, cloud: bool = False) -> Path:
    """Export a supported object to one file without serializing model internals.

    Cloud receives typed JSON documents (``table.json``, ``model.json``, ``test.json``) and
    SVG figures. Buckets receive CSV tables, PNG figures, and HTML model summaries. Both
    receive Styler tables as HTML, plotly figures as HTML, and altair charts as Vega-Lite.
    """
    from framework import _artifacts

    path = _artifacts.rendered(source, directory, cloud)
    if path is not None:
        return path
    if cloud:
        document = _artifacts.structured(source)
        if document is not None:
            path = directory / f"{document['kind']}.json"
            path.write_text(json.dumps(document, allow_nan=False), encoding="utf-8")
            return path
        raise FrameworkError(_ACCEPTED)
    frame = _artifacts.as_frame(source)
    if frame is not None:
        path = directory / "table.csv"
        _artifacts.frame_for_export(frame).to_csv(path, index=False)
        return path
    if type(source).__module__.startswith("statsmodels") and callable(
        getattr(source, "summary", None)
    ):
        path = directory / "model.html"
        summary = source.summary().as_html()
        path.write_text(
            '<!doctype html><html><head><meta charset="utf-8"><title>Model results</title>'
            "</head><body>" + summary + "</body></html>",
            encoding="utf-8",
        )
        return path
    document = _artifacts.structured(source)
    if document is not None:
        path = directory / f"{document['kind']}.json"
        path.write_text(json.dumps(document, allow_nan=False), encoding="utf-8")
        return path
    raise FrameworkError(_ACCEPTED)


def _publish_legacy(
    source: Any,
    dest: str | None = None,
    connection: str | None = None,
    overwrite: bool = True,
    self_contained: bool = True,
    slug: str | bool | None = None,
    include_private: bool = False,
    *,
    to: str | None = None,
    allow_private: bool | None = None,
    title: str | None = None,
    description: str | None = None,
    kind: str | None = None,
    password: str | None = None,
    visibility: str | None = None,
    collection: str | int | None = None,
    share_unlisted: bool = False,
    _on_s3: Callable[[Path, str | list[str]], None] | None = None,
) -> str | list[str]:
    """Upload a file or folder to the project's storage bucket.

    Args:
        source: A file, a rendered folder, or an object. Accepted objects are pandas and
            polars DataFrames and Series, numpy arrays with one or two dimensions, pandas
            Styler tables, scipy.stats test results, statsmodels results, fitted
            scikit-learn, xgboost, and lightgbm models, matplotlib Figures and Axes, seaborn
            plots and grids, plotly Figures, and altair charts. On Framework Cloud, tables
            become typed tables with column summaries (every row is kept), tests and models
            become structured summaries, and static plots become SVG. Model exports carry
            coefficients, importance, and fit statistics, never observations, residuals, or
            serialized models. Buckets receive CSV tables, PNG figures, and HTML model
            summaries; Styler tables, plotly figures, and altair charts keep their HTML or
            Vega-Lite form everywhere.
        dest: Where to put it in the bucket. Default: the source's name.
        connection: A storage bucket name. Default: the project's default bucket.
        overwrite: Accepted for parity with R; uploads always replace.
        self_contained: For Framework Cloud publishing.
        slug: For Framework Cloud publishing.
        include_private: Upload files with "private" in their path without asking. Only a
            person who has reviewed the files should set this.
        to: "cloud" explicitly publishes to Framework Cloud and "bucket" selects storage.
            "github" publishes to the project's GitHub Pages site: each publish is kept in
            ``published/<slug>/v/<number>/``, the latest also sits at ``published/<slug>/``,
            only those and the site index are committed, and the commit is pushed. Pages
            sites are public, even from a private repository. Default: a storage bucket or
            Framework Cloud.
        title: Artifact title on Framework Cloud.
        description: Artifact description on Framework Cloud.
        kind: Optional artifact kind override on Framework Cloud.
        password: Password required to view an artifact on Framework Cloud.
        visibility: "public", "unlisted", or "private" on Framework Cloud. Default: a new
            artifact is unlisted and an existing one keeps its setting. It wins over
            ``slug=True`` or ``False``.
        collection: Existing project collection ID or slug; follows the latest artifact version.
        share_unlisted: Explicitly allow collection readers to access an unlisted artifact.
        allow_private: Deprecated name for ``include_private``.

    Returns:
        The public URL, or a list of URLs for a folder.
    """
    if share_unlisted and collection is None:
        raise FrameworkError("share_unlisted requires a collection.")
    if not isinstance(source, (str, Path)):
        with tempfile.TemporaryDirectory(prefix="fw_artifact_") as temp:
            is_cloud = to != "github" and _target(connection, to) == "cloud"
            path = _artifact_file(source, Path(temp), cloud=is_cloud)
            if kind is None and is_cloud and path.name in ("table.json", "model.json"):
                kind = path.stem
            return _publish_legacy(
                path,
                dest=dest,
                connection=connection,
                overwrite=overwrite,
                self_contained=self_contained,
                slug=slug,
                include_private=include_private,
                to=to,
                allow_private=allow_private,
                title=title,
                description=description,
                kind=kind,
                password=password,
                visibility=visibility,
                collection=collection,
                share_unlisted=share_unlisted,
                _on_s3=_on_s3,
            )
    path = Path(source)
    if not path.exists():
        raise FrameworkError(f"Source not found: {path}")
    include = include_private_arg(include_private, allow_private)
    confirm_private_upload(_files(path, True, None) if path.is_dir() else [path], include)
    if to == "github":
        if any(v is not None for v in (title, description, kind, password, visibility, collection)):
            raise FrameworkError("Artifact metadata requires publishing to Framework Cloud.")
        return _publish_github(to, path, dest, connection, slug, self_contained)
    if _target(connection, to) == "cloud":
        from framework.cloud import cloud_publish

        return cloud_publish(
            path,
            dest=dest,
            self_contained=self_contained,
            slug=slug,
            include_private=True,
            title=title or doc_title(path),
            description=description,
            kind=kind,
            password=password,
            visibility=visibility,
            collection=collection,
            share_unlisted=share_unlisted,
        )
    if any(v is not None for v in (title, description, kind, password, visibility, collection)):
        raise FrameworkError("Artifact metadata requires publishing to Framework Cloud.")
    _slug_ignored(slug)
    bucket = resolve_bucket(connection)
    dest = dest or path.name
    if path.is_dir():
        urls = _upload_folder(path, dest, bucket, _files(path, True, None))
        inform(f"Published {len(urls)} files to S3")
        if _on_s3 is not None:
            _on_s3(path, urls)
        return urls
    url = upload_file(path, dest, bucket)
    inform(f"Published: {url}")
    if _on_s3 is not None:
        _on_s3(path, url)
    return url


def publish(
    source: Any,
    dest: str | None = None,
    connection: str | None = None,
    overwrite: bool = True,
    self_contained: bool = True,
    slug: str | bool | None = None,
    include_private: bool = False,
    *,
    to: str | None = None,
    allow_private: bool | None = None,
    title: str | None = None,
    description: str | None = None,
    kind: str | None = None,
    password: str | None = None,
    visibility: str | None = None,
    collection: str | int | None = None,
    share_unlisted: bool = False,
) -> str | list[str]:
    """Publish an artifact to a named destination or the project default.

    Args:
        source: A file, a rendered folder, or an object. Accepted objects are pandas and
            polars DataFrames and Series, numpy arrays with one or two dimensions, pandas
            Styler tables, scipy.stats test results, statsmodels results, fitted
            scikit-learn, xgboost, and lightgbm models, matplotlib Figures and Axes, seaborn
            plots and grids, plotly Figures, and altair charts. On Framework Cloud, tables
            become typed tables with column summaries (every row is kept), tests and models
            become structured summaries, and static plots become SVG. Model exports carry
            coefficients, importance, and fit statistics, never observations, residuals, or
            serialized models. Buckets receive CSV tables, PNG figures, and HTML model
            summaries; Styler tables, plotly figures, and altair charts keep their HTML or
            Vega-Lite form everywhere.
        dest: Where to put it in the bucket. Default: the source's name.
        connection: A storage bucket name. Default: the project's default bucket.
        overwrite: Accepted for parity with R; uploads always replace.
        self_contained: For Framework Cloud publishing.
        slug: For Framework Cloud publishing.
        include_private: Upload files with "private" in their path without asking. Only a
            person who has reviewed the files should set this.
        to: A configured destination name, cloud, github, folder, s3, or bucket.
            Named destinations take priority over publish.default and legacy defaults.
            "cloud" explicitly publishes to Framework Cloud and "bucket" selects storage.
            "github" publishes to the project's GitHub Pages site: each publish is kept in
            ``published/<slug>/v/<number>/``, the latest also sits at ``published/<slug>/``,
            only those and the site index are committed, and the commit is pushed. Pages
            sites are public, even from a private repository. Default: a storage bucket or
            Framework Cloud.
        title: Artifact title on Framework Cloud.
        description: Artifact description on Framework Cloud.
        kind: Optional artifact kind override on Framework Cloud.
        password: Password required to view an artifact on Framework Cloud.
        visibility: "public", "unlisted", or "private" on Framework Cloud. Default: a new
            artifact is unlisted and an existing one keeps its setting. It wins over
            ``slug=True`` or ``False``.
        collection: Existing project collection ID or slug; follows the latest artifact version.
        share_unlisted: Explicitly allow collection readers to access an unlisted artifact.
        allow_private: Deprecated name for ``include_private``.

    Returns:
        The public URL, or a list of URLs for a folder.
    """
    from framework.publish_bundle import (
        Destination,
        build_bundle,
        destination,
        publish_external,
        validate,
    )

    if share_unlisted and collection is None:
        raise FrameworkError("share_unlisted requires a collection.")
    selected = None if to == "bucket" else destination(to, connection)
    if selected is not None:
        visibility = visibility or selected.options.get("visibility")
        validate(selected, visibility, password, collection)
        if connection is not None and selected.driver != "s3":
            raise FrameworkError("connection is only used with a storage bucket.")
        own = selected.options
        if (
            connection is not None
            and selected.driver == "s3"
            and (
                own.get("region")
                or own.get("endpoint")
                or own.get("bucket") not in (None, connection)
            )
        ):
            # The destination names its own bucket, so connection would be ignored
            raise FrameworkError(
                f'The "{selected.name}" destination sets its own bucket. Leave out connection, '
                "or remove the bucket from the destination."
            )
    else:
        driver = _target(connection, to)
        validate(Destination(driver, driver, {}), visibility, password, collection)
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FrameworkError(f"Source not found: {path}")
        files = _files(path, True, None) if path.is_dir() else [path]
        if path.is_file():
            assets = path.with_name(f"{path.stem}_files")
            if assets.is_dir():
                files += _files(assets, True, None)
        confirm_private_upload(files, include_private_arg(include_private, allow_private))
    with tempfile.TemporaryDirectory(prefix="fw_publish_bundle_") as temp:
        bundle = Path(temp) / "bundle"
        manifest = build_bundle(
            source,
            bundle,
            slug=slug if isinstance(slug, str) else None,
            title=title,
            description=description,
            kind=kind,
            self_contained=self_contained,
        )
        if selected is not None and selected.driver != "cloud":
            if dest is not None:
                raise FrameworkError(
                    f'Not used with to="{selected.driver}": dest. Name the folder with slug.'
                )
            if isinstance(slug, bool):
                raise FrameworkError("slug=True/False is not used with bundle destinations.")
            return publish_external(
                source,
                selected,
                slug=slug,
                title=title,
                description=description,
                kind=kind,
                visibility=visibility,
                self_contained=self_contained,
                connection=connection,
                built_bundle=bundle,
                built_manifest=manifest,
            )

        def record_legacy(path: Path, published: str | list[str]) -> None:
            import hashlib

            from framework.publish_bundle import track_external

            paths = _files(path, True, None) if path.is_dir() else [path]
            if path.is_file():
                checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                checksum = hashlib.sha256(
                    json.dumps(
                        [
                            {
                                "path": item.relative_to(path).as_posix(),
                                "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
                            }
                            for item in paths
                        ],
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
            receipt = {
                "driver": "s3",
                "version": 1,
                "version_url": None,
                "url": published if isinstance(published, str) else next(iter(published), None),
                "size_bytes": sum(item.stat().st_size for item in paths),
                "sha256": checksum,
            }
            track_external(manifest, receipt, bundle, "public")

        return _publish_legacy(
            source,
            dest=dest,
            connection=connection,
            overwrite=overwrite,
            self_contained=self_contained,
            slug=slug,
            include_private=True,
            to="cloud" if selected is not None else to,
            title=title,
            description=description,
            kind=kind,
            password=password,
            visibility=visibility,
            collection=collection,
            share_unlisted=share_unlisted,
            _on_s3=record_legacy,
        )


def publish_notebook(
    file: str | Path,
    dest: str | None = None,
    connection: str | None = None,
    self_contained: bool = True,
    format: str = "html",
    slug: str | bool | None = None,
    include_private: bool = False,
    *,
    to: str | None = None,
    allow_private: bool | None = None,
    collection: str | int | None = None,
    share_unlisted: bool = False,
    title: str | None = None,
    description: str | None = None,
) -> str:
    """Render a Quarto notebook with the ``quarto`` command and upload the result.

    A bucket with ``static_hosting: true`` gets ``<dest>/index.html`` and a
    clean URL ending in ``/``. Other buckets get ``<dest>.html`` as one
    self-contained file.

    With no ``connection`` and no buckets in the settings, a project with a Framework Cloud
    token publishes to the cloud instead.

    Args:
        file: An existing ``.qmd`` or ``.ipynb`` file.
        dest: Where to put the result in the bucket, without ``.html``. Default: the file's
            name without its extension. On Framework Cloud it names the document when ``slug``
            is not a string.
        connection: A storage bucket name from the settings. Setting it always publishes to a
            bucket. Default: the project's default bucket, or Framework Cloud when no bucket is
            configured.
        self_contained: Embed resources in one HTML file. Buckets without static hosting are
            always self-contained. With static hosting, False uploads the render folder.
        format: The Quarto output format passed to ``--to``. Framework Cloud accepts only
            "html". Default: "html".
        slug: For Framework Cloud: a string names the document and leaves its visibility
            alone, True turns the readable URL on with the derived name, and False turns it
            off. A storage bucket ignores it with a warning.
        include_private: Upload files with "private" in their path without asking. Only a
            person who has reviewed the files should set this.
        to: "cloud" explicitly publishes to Framework Cloud and "bucket" selects storage.
            "github" publishes to the project's GitHub Pages site: each publish is kept in
            ``published/<slug>/v/<number>/``, the latest also sits at ``published/<slug>/``,
            only those and the site index are committed, and the commit is pushed. Pages
            sites are public, even from a private repository. Default: a storage bucket or
            Framework Cloud.
        collection: Existing project collection ID or slug on Framework Cloud.
        share_unlisted: Explicitly allow collection readers to access an unlisted artifact.
        allow_private: Deprecated name for ``include_private``.
        title: The published title, on Framework Cloud and named destinations. Default: the
            ``title`` in the document's YAML front matter, else its file name.
        description: A short description, on Framework Cloud and named destinations.

    Returns:
        The public URL.

    Raises:
        FrameworkError: When the file is missing or not a notebook, has "private" in its path
            and is not confirmed, Quarto is not installed, rendering fails, or there is nowhere
            to publish.
    """
    from framework.publish_bundle import destination

    selected = None if to == "bucket" else destination(to, connection)
    if selected is not None:
        source_path = Path(file)
        if not source_path.is_file() or source_path.suffix.lower() not in (".qmd", ".ipynb"):
            raise FrameworkError("publish_notebook() needs an existing .qmd or .ipynb file.")
        if format != "html":
            raise FrameworkError("Bundle publishing accepts HTML only.")
        published = publish(
            file,
            dest=dest,
            connection=connection,
            self_contained=self_contained,
            slug=slug,
            include_private=include_private,
            to=to,
            allow_private=allow_private,
            collection=collection,
            share_unlisted=share_unlisted,
            title=title,
            description=description,
        )
        assert isinstance(published, str)
        return published
    if share_unlisted and collection is None:
        raise FrameworkError("share_unlisted requires a collection.")
    if collection is not None and to == "github":
        raise FrameworkError("Collections require publishing to Framework Cloud.")
    source = Path(file)
    if not source.is_file() or source.suffix.lower() not in (".qmd", ".ipynb"):
        raise FrameworkError(f"publish_notebook() needs an existing .qmd or .ipynb file: {source}")
    confirm_private_upload([source], include_private_arg(include_private, allow_private))
    if to == "github":
        if format != "html":
            raise FrameworkError(f'GitHub Pages publishes HTML only (got format="{format}").')
        if title is not None or description is not None:
            raise FrameworkError("Artifact metadata requires publishing to Framework Cloud.")
        return _publish_github(to, source, dest, connection, slug, self_contained)
    quarto = shutil.which("quarto")
    if quarto is None:
        raise FrameworkError(
            "Quarto not found. Install it from https://quarto.org/docs/get-started/"
        )
    if _target(connection, to) == "cloud":
        if format != "html":
            raise FrameworkError(f'Framework Cloud publishes HTML only (got format="{format}").')
        from framework.cloud import cloud_publish

        return cloud_publish(
            source,
            dest=dest,
            self_contained=self_contained,
            slug=slug,
            include_private=True,
            collection=collection,
            share_unlisted=share_unlisted,
            title=title or doc_title(source),
            description=description,
        )
    if collection is not None:
        raise FrameworkError("Collections require publishing to Framework Cloud.")
    if title is not None or description is not None:
        raise FrameworkError("Artifact metadata requires publishing to Framework Cloud.")
    _slug_ignored(slug)
    bucket = resolve_bucket(connection)
    self_contained = self_contained or not bucket.static_hosting

    with tempfile.TemporaryDirectory(prefix="publish_notebook_") as temp:
        command = [quarto, "render", str(source.resolve()), "--output-dir", temp, "--to", format]
        if self_contained:
            command.append("--embed-resources")
        inform(f"Rendering {source.name}...")
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise FrameworkError(f"Quarto render failed:\n{result.stdout}{result.stderr}")
        # A project's _quarto.yml keeps the notebook's folder under the output folder
        page = rendered_page(Path(temp), source.stem)
        if page is None:
            raise FrameworkError("No HTML output found after rendering")
        root = page.parent
        base = re.sub(r"\.html$", "", (dest or source.stem).rstrip("/"))

        if bucket.static_hosting:
            if self_contained:
                upload_file(page, f"{base}/index.html", bucket)
            else:
                index = root / "index.html"
                if page != index:
                    page.rename(index)
                urls = _upload_folder(root, base, bucket, _files(root, True, None))
                inform(f"Published {len(urls)} files")
            url = bucket.url(bucket.key(f"{base}/"))
        else:
            url = upload_file(page, f"{base}.html", bucket)
    inform(f"Published: {url}")
    return url


def _publish_route(to: str | None, connection: str | None) -> tuple[Any, str | None]:
    """Where publish_data(), publish_dir(), and publish_list() go, as in R.

    Like publish(), they follow ``to``, then ``publish.default``. An explicit
    ``connection``, ``to="s3"`` or ``"bucket"``, an S3 destination, or a project without
    ``publish.default`` keep the plain bucket upload. Returns the named destination (None
    for a bucket) and the bucket name.
    """
    from framework.publish_bundle import destination

    if to in ("s3", "bucket"):
        return None, connection
    if to is None:
        if connection is not None:
            return None, connection
        publish = settings_read().get("publish")
        if not isinstance(publish, dict) or not publish.get("default"):
            return None, None
    selected = destination(to, connection)
    if selected is None or selected.driver == "s3":
        bucket = selected.options.get("bucket") if selected is not None else None
        return None, str(bucket) if bucket else connection
    return selected, None


def _data_slug(dest: str) -> str:
    """publish_data()'s slug: ``dest`` without its extension, with each ``/`` as ``-``.

    ``"tables/demo.csv"`` becomes ``"tables-demo"``, so the folder part is kept.
    """
    path = PurePosixPath(dest.replace("\\", "/").removesuffix(".gz"))
    if path.name:
        path = path.with_suffix("")
    return "-".join(part for part in path.parts if part not in ("", ".", "..", "/"))


def publish_data(
    data: pd.DataFrame | str | Path,
    dest: str,
    format: str = "csv",
    connection: str | None = None,
    compress: bool = False,
    include_private: bool = False,
    *,
    to: str | None = None,
    allow_private: bool | None = None,
    title: str | None = None,
    description: str | None = None,
) -> str:
    """Upload a DataFrame (as CSV, Parquet, or JSON) or a data file.

    With ``compress``, the file is gzipped and ``.gz`` is added to ``dest``.

    Like ``publish()``, it follows ``to``, then ``publish.default``. An S3 destination, an
    explicit ``connection``, or a project without ``publish.default`` gets a plain upload
    to the bucket at ``dest``. Folder, GitHub, and Cloud destinations publish with
    ``publish()``: a DataFrame becomes the same typed table ``publish()`` makes of it, and a
    file is published under ``dest``'s file name. The slug is ``dest`` without its
    extension, with each ``/`` turned into ``-``: ``"tables/demo.csv"`` publishes as
    ``tables-demo``.

    Args:
        data: A DataFrame to write in ``format``, or the path of a file to upload as is.
        dest: The object's path in the bucket, including any extension. For other
            destinations, it names the slug, and its file name names a published file.
        format: How a DataFrame is written to a bucket. One of "csv", "parquet", or "json"
            (records). Default: "csv". It is checked for files too, but does not change them.
        connection: A storage bucket name from the settings. Default: the project's default
            bucket, then one marked ``default: true``, then the first.
        compress: Gzip the file before uploading.
        include_private: Upload files with "private" in their path without asking. Only a
            person who has reviewed the files should set this.
        to: A destination name from ``publish.destinations``, or a driver name, as in
            ``publish()``. Default: ``publish.default``.
        allow_private: Deprecated name for ``include_private``.
        title: The published title, on Framework Cloud and named destinations. Default: the
            slug.
        description: A short description, on Framework Cloud and named destinations.

    Returns:
        The public URL (for a folder destination without a ``url``, its location).

    Raises:
        FrameworkError: When ``format`` is not allowed, the file is missing or has "private"
            in its path and is not confirmed, no bucket is configured, the upload fails, or
            a title or description is given for a plain bucket upload.
    """
    if format not in ("csv", "parquet", "json"):
        raise FrameworkError("format must be csv, parquet, or json (rds is R-only)")
    include = include_private_arg(include_private, allow_private)
    selected, bucket_name = _publish_route(to, connection)
    if selected is None and (title is not None or description is not None):
        raise FrameworkError("Artifact metadata requires publishing to Framework Cloud.")
    if selected is not None and isinstance(data, pd.DataFrame):
        # The same typed table publish() makes of a DataFrame
        slug = _data_slug(dest)
        inform(f"Publishing {dest} as {slug}")
        published = publish(
            data,
            to=selected.name,
            slug=slug,
            include_private=True,
            title=title,
            description=description,
        )
        assert isinstance(published, str)
        return published
    with tempfile.TemporaryDirectory(prefix="publish_data_") as temp:
        if isinstance(data, pd.DataFrame):
            source = Path(temp) / f"data.{format}"
            if format == "csv":
                data.to_csv(source, index=False, lineterminator="\n")
            elif format == "parquet":
                data.to_parquet(source, index=False)
            else:
                data.to_json(source, orient="records")
        else:
            source = Path(data)
            if not source.is_file():
                raise FrameworkError(f"File not found: {source}")
            confirm_private_upload([source], include)
        if compress:
            compressed = Path(temp) / f"{source.name}.gz"
            with source.open("rb") as raw, gzip.open(compressed, "wb") as packed:
                shutil.copyfileobj(raw, packed)
            source = compressed
            dest = dest if dest.endswith(".gz") else f"{dest}.gz"
        if selected is not None:
            # Folder, GitHub, and Cloud: publish the file under dest's file name
            staged = Path(temp) / "stage" / Path(dest).name
            staged.parent.mkdir()
            shutil.copy2(source, staged)
            slug = _data_slug(dest)
            inform(f"Publishing {dest} as {slug}")
            published = publish(
                staged,
                to=selected.name,
                slug=slug,
                include_private=True,
                title=title,
                description=description,
            )
            assert isinstance(published, str)
            return published
        url = upload_file(source, dest, resolve_bucket(bucket_name))
    inform(f"Published: {url}")
    return url


def publish_dir(
    dir: str | Path,
    dest: str | None = None,
    connection: str | None = None,
    pattern: str | None = None,
    recursive: bool = True,
    include_private: bool = False,
    *,
    to: str | None = None,
    allow_private: bool | None = None,
    title: str | None = None,
    description: str | None = None,
) -> list[str]:
    """Upload the files in a folder. ``pattern`` is a regular expression on the paths.

    Like ``publish()``, it follows ``to``, then ``publish.default``. An S3 destination, an
    explicit ``connection``, or a project without ``publish.default`` gets a file-by-file
    upload under ``dest``. Folder, GitHub, and Cloud destinations publish the folder (only
    the files ``pattern`` and ``recursive`` select) with ``publish()``, with the last part
    of ``dest`` (or the folder's name) as the slug.

    Args:
        dir: The folder to upload.
        dest: The folder in the bucket. Each file keeps its path relative to ``dir``. Default:
            the folder's name.
        connection: A storage bucket name from the settings. Default: the project's default
            bucket, then one marked ``default: true``, then the first.
        pattern: Only upload files whose path inside ``dir`` matches this regular expression
            (searched anywhere in that path, such as ``"figures/.*\\.png$"``). Default: every
            file.
        recursive: Include files in subfolders.
        include_private: Upload files with "private" in their path without asking. Only a
            person who has reviewed the files should set this.
        to: A destination name from ``publish.destinations``, or a driver name, as in
            ``publish()``. Default: ``publish.default``.
        allow_private: Deprecated name for ``include_private``.
        title: The published title, on Framework Cloud and named destinations. Default: the
            slug.
        description: A short description, on Framework Cloud and named destinations.

    Returns:
        The public URL of each file uploaded. The list is empty when no file matched. For
        other destinations, a list with the published artifact's URL or location.

    Raises:
        FrameworkError: When the folder is missing, a file has "private" in its path and is
            not confirmed, no bucket is configured, an upload fails, or a title or description
            is given for a plain bucket upload.
    """
    folder = Path(dir)
    if not folder.is_dir():
        raise FrameworkError(f"Directory not found: {folder}")
    include = include_private_arg(include_private, allow_private)
    files = _files(folder, recursive, pattern)
    if not files:
        inform("No files found to upload")
        return []
    confirm_private_upload(files, include)
    selected, bucket_name = _publish_route(to, connection)
    if selected is None and (title is not None or description is not None):
        raise FrameworkError("Artifact metadata requires publishing to Framework Cloud.")
    dest = dest or folder.resolve().name
    if selected is not None:
        slug = Path(dest.rstrip("/")).name
        if pattern is None and recursive:
            published = publish(
                folder,
                to=selected.name,
                slug=slug,
                include_private=True,
                title=title,
                description=description,
            )
        else:
            # Publish only the selected files, keeping their paths inside the folder
            with tempfile.TemporaryDirectory(prefix="fw_publish_dir_") as temp:
                stage = Path(temp) / slug
                for file in files:
                    target = stage / file.relative_to(folder)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, target)
                published = publish(
                    stage,
                    to=selected.name,
                    slug=slug,
                    include_private=True,
                    title=title,
                    description=description,
                )
        return [published] if isinstance(published, str) else list(published)
    bucket = resolve_bucket(bucket_name)
    inform(f"Uploading {len(files)} files...")
    urls = _upload_folder(folder, dest, bucket, files)
    inform(f"Published {len(urls)} files to {dest}")
    return urls


def _folder_list(selected: Any, prefix: str | None, max: int) -> pd.DataFrame:
    """publish_list() for a folder destination: one row per published artifact."""
    from framework.publish_bundle import folder_path

    root = folder_path(selected.options["path"])
    base = selected.options.get("url")
    rows = []
    for file in sorted(root.glob("*/artifact.json")) if root.is_dir() else []:
        try:
            manifest = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        slug = manifest.get("slug") if isinstance(manifest, dict) else None
        if not isinstance(slug, str):
            continue
        rows.append(
            {
                "key": f"{slug}/",
                "size": sum(int(f.get("bytes") or 0) for f in manifest.get("files") or []),
                "last_modified": manifest.get("created_at"),
                "url": f"{str(base).rstrip('/')}/{slug}/"
                if base
                else str(root / slug / "index.html"),
            }
        )
    frame = pd.DataFrame(rows, columns=["key", "size", "last_modified", "url"])
    if prefix is not None:
        frame = frame[frame["key"].str.startswith(prefix)].reset_index(drop=True)
    return frame.head(max)


def publish_list(
    prefix: str | None = None,
    connection: str | None = None,
    max: int = 1000,
    *,
    to: str | None = None,
) -> pd.DataFrame:
    """Objects in the bucket (under its prefix): key, size, last_modified, and url.

    Like ``publish()``, it follows ``to``, then ``publish.default``. For a folder
    destination it lists the published artifacts, one row per slug. GitHub and Cloud
    destinations can't be listed here: Framework Cloud artifacts are on the server's
    Artifacts page, ``<server>/published``.

    Args:
        prefix: Only list objects under this path, which is added to the bucket's prefix.
            Default: everything under the bucket's prefix.
        connection: A storage bucket name from the settings. Default: the project's default
            bucket, then one marked ``default: true``, then the first.
        max: The most objects to list. Default: 1000.
        to: A destination name from ``publish.destinations``, or a driver name, as in
            ``publish()``. Default: ``publish.default``.

    Returns:
        A DataFrame with columns key, size, last_modified, and url.

    Raises:
        FrameworkError: When no bucket is configured, listing fails, or the destination is
            a GitHub or Cloud destination.
    """
    selected, bucket_name = _publish_route(to, connection)
    if selected is not None:
        if selected.driver == "folder":
            return _folder_list(selected, prefix, max)
        raise FrameworkError(
            f"publish_list() lists S3 and folder destinations. '{selected.name}' uses the "
            f"{selected.driver} driver."
        )
    connection = bucket_name
    if connection is None and not bucket_configs() and _has_project_token():
        from framework.cloud import cloud_server

        server = cloud_server(scope="project").url
        raise FrameworkError(
            "publish_list() lists uploads to S3 storage buckets, and this project has none. "
            f"See your Framework Cloud artifacts on the Artifacts page: {server}/published"
        )
    bucket = resolve_bucket(connection)
    full_prefix = bucket.key(prefix) if prefix else bucket.prefix
    try:
        response = bucket.client().list_objects_v2(
            Bucket=bucket.bucket, Prefix=full_prefix, MaxKeys=max
        )
    except Exception as error:
        raise FrameworkError(f"Failed to list S3 objects: {error}") from error
    rows = [
        {
            "key": item["Key"],
            "size": item.get("Size"),
            "last_modified": str(item.get("LastModified", "")),
            "url": bucket.url(item["Key"]),
        }
        for item in response.get("Contents", [])
    ]
    return pd.DataFrame(rows, columns=["key", "size", "last_modified", "url"])


def storage_test(connection: str | None = None) -> bool:
    """Whether the bucket exists and the credentials can reach it.

    Args:
        connection: A storage bucket name from the settings. Default: the project's default
            bucket, then one marked ``default: true``, then the first.

    Returns:
        True when the bucket answers, False when the request fails.

    Raises:
        FrameworkError: When no bucket is configured, the named one is not found, or its
            credentials are missing. These are setup problems, so they raise rather than
            return False.
    """
    bucket = resolve_bucket(connection)
    try:
        bucket.client().head_bucket(Bucket=bucket.bucket)
    except Exception as error:
        inform(f"S3 connection failed: {error}")
        return False
    inform(f"S3 connection OK: {bucket.name} (bucket: {bucket.bucket})")
    return True
