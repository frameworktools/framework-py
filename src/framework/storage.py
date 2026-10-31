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
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from framework._log import inform
from framework._private import confirm_private_upload, include_private_arg
from framework.errors import FrameworkError
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
                "Install it with: pip install framework-py[s3]"
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
        files = [p for p in files if re.search(pattern, p.as_posix())]
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


def _target(connection: str | None) -> str:
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


def _slug_ignored(slug: str | bool | None) -> None:
    if slug is not None:
        import warnings

        from framework.errors import FrameworkWarning

        warnings.warn(
            "`slug` applies to Framework Cloud only; ignored for storage buckets.",
            FrameworkWarning,
            stacklevel=3,
        )


def publish(
    source: str | Path,
    dest: str | None = None,
    connection: str | None = None,
    overwrite: bool = True,
    self_contained: bool = True,
    slug: str | bool | None = None,
    include_private: bool = False,
    *,
    allow_private: bool | None = None,
) -> str | list[str]:
    """Upload a file or folder to the project's storage bucket.

    Args:
        source: A file or folder.
        dest: Where to put it in the bucket. Default: the source's name.
        connection: A storage bucket name. Default: the project's default bucket.
        overwrite: Accepted for parity with R; uploads always replace.
        self_contained: For Framework Cloud publishing.
        slug: For Framework Cloud publishing.
        include_private: Upload files with "private" in their path without asking. Only a
            person who has reviewed the files should set this.
        allow_private: Deprecated name for ``include_private``.

    Returns:
        The public URL, or a list of URLs for a folder.
    """
    path = Path(source)
    if not path.exists():
        raise FrameworkError(f"Source not found: {path}")
    include = include_private_arg(include_private, allow_private)
    confirm_private_upload(_files(path, True, None) if path.is_dir() else [path], include)
    if _target(connection) == "cloud":
        from framework.cloud import cloud_publish

        return cloud_publish(
            path, dest=dest, self_contained=self_contained, slug=slug, include_private=True
        )
    _slug_ignored(slug)
    bucket = resolve_bucket(connection)
    dest = dest or path.name
    if path.is_dir():
        urls = _upload_folder(path, dest, bucket, _files(path, True, None))
        inform(f"Published {len(urls)} files to S3")
        return urls
    url = upload_file(path, dest, bucket)
    inform(f"Published: {url}")
    return url


def publish_notebook(
    file: str | Path,
    dest: str | None = None,
    connection: str | None = None,
    self_contained: bool = True,
    format: str = "html",
    slug: str | bool | None = None,
    include_private: bool = False,
    *,
    allow_private: bool | None = None,
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
        slug: For Framework Cloud: a string names the document and turns on its readable URL,
            True uses the derived name, and False turns the readable URL off. A storage bucket
            ignores it with a warning.
        include_private: Upload files with "private" in their path without asking. Only a
            person who has reviewed the files should set this.
        allow_private: Deprecated name for ``include_private``.

    Returns:
        The public URL.

    Raises:
        FrameworkError: When the file is missing or not a notebook, has "private" in its path
            and is not confirmed, Quarto is not installed, rendering fails, or there is nowhere
            to publish.
    """
    source = Path(file)
    if not source.is_file() or source.suffix.lower() not in (".qmd", ".ipynb"):
        raise FrameworkError(f"publish_notebook() needs an existing .qmd or .ipynb file: {source}")
    confirm_private_upload([source], include_private_arg(include_private, allow_private))
    quarto = shutil.which("quarto")
    if quarto is None:
        raise FrameworkError(
            "Quarto not found. Install it from https://quarto.org/docs/get-started/"
        )
    if _target(connection) == "cloud":
        if format != "html":
            raise FrameworkError(f'Framework Cloud publishes HTML only (got format="{format}").')
        from framework.cloud import cloud_publish

        return cloud_publish(
            source, dest=dest, self_contained=self_contained, slug=slug, include_private=True
        )
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
        outputs = sorted(Path(temp).glob("*.html"))
        if not outputs:
            raise FrameworkError("No HTML output found after rendering")
        base = re.sub(r"\.html$", "", (dest or source.stem).rstrip("/"))

        if bucket.static_hosting:
            if self_contained:
                upload_file(outputs[0], f"{base}/index.html", bucket)
            else:
                index = Path(temp) / "index.html"
                if outputs[0] != index:
                    outputs[0].rename(index)
                urls = _upload_folder(Path(temp), base, bucket, _files(Path(temp), True, None))
                inform(f"Published {len(urls)} files")
            url = bucket.url(bucket.key(f"{base}/"))
        else:
            url = upload_file(outputs[0], f"{base}.html", bucket)
    inform(f"Published: {url}")
    return url


def publish_data(
    data: pd.DataFrame | str | Path,
    dest: str,
    format: str = "csv",
    connection: str | None = None,
    compress: bool = False,
    include_private: bool = False,
    *,
    allow_private: bool | None = None,
) -> str:
    """Upload a DataFrame (as CSV, Parquet, or JSON) or a data file.

    With ``compress``, the file is gzipped and ``.gz`` is added to ``dest``.

    Args:
        data: A DataFrame to write in ``format``, or the path of a file to upload as is.
        dest: The object's path in the bucket, including any extension.
        format: How a DataFrame is written. One of "csv", "parquet", or "json" (records).
            Default: "csv". It is checked for files too, but does not change them.
        connection: A storage bucket name from the settings. Default: the project's default
            bucket, then one marked ``default: true``, then the first.
        compress: Gzip the file before uploading.
        include_private: Upload files with "private" in their path without asking. Only a
            person who has reviewed the files should set this.
        allow_private: Deprecated name for ``include_private``.

    Returns:
        The public URL.

    Raises:
        FrameworkError: When ``format`` is not allowed, the file is missing or has "private"
            in its path and is not confirmed, no bucket is configured, or the upload fails.
    """
    if format not in ("csv", "parquet", "json"):
        raise FrameworkError("format must be csv, parquet, or json (rds is R-only)")
    include = include_private_arg(include_private, allow_private)
    bucket = resolve_bucket(connection)
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
        url = upload_file(source, dest, bucket)
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
    allow_private: bool | None = None,
) -> list[str]:
    """Upload the files in a folder. ``pattern`` is a regular expression on the paths.

    Args:
        dir: The folder to upload.
        dest: The folder in the bucket. Each file keeps its path relative to ``dir``. Default:
            the folder's name.
        connection: A storage bucket name from the settings. Default: the project's default
            bucket, then one marked ``default: true``, then the first.
        pattern: Only upload files whose path matches this regular expression (searched
            anywhere in the path). Default: every file.
        recursive: Include files in subfolders.
        include_private: Upload files with "private" in their path without asking. Only a
            person who has reviewed the files should set this.
        allow_private: Deprecated name for ``include_private``.

    Returns:
        The public URL of each file uploaded. The list is empty when no file matched.

    Raises:
        FrameworkError: When the folder is missing, a file has "private" in its path and is
            not confirmed, no bucket is configured, or an upload fails.
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
    bucket = resolve_bucket(connection)
    dest = dest or folder.resolve().name
    inform(f"Uploading {len(files)} files...")
    urls = _upload_folder(folder, dest, bucket, files)
    inform(f"Published {len(urls)} files to {dest}")
    return urls


def publish_list(
    prefix: str | None = None, connection: str | None = None, max: int = 1000
) -> pd.DataFrame:
    """Objects in the bucket (under its prefix): key, size, last_modified, and url.

    Args:
        prefix: Only list objects under this path, which is added to the bucket's prefix.
            Default: everything under the bucket's prefix.
        connection: A storage bucket name from the settings. Default: the project's default
            bucket, then one marked ``default: true``, then the first.
        max: The most objects to list. Default: 1000.

    Returns:
        A DataFrame with columns key, size, last_modified, and url.

    Raises:
        FrameworkError: When no bucket is configured or listing fails.
    """
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
        FrameworkError: When no bucket is configured or the named one is not found.
    """
    bucket = resolve_bucket(connection)
    try:
        bucket.client().head_bucket(Bucket=bucket.bucket)
    except Exception as error:
        inform(f"S3 connection failed: {error}")
        return False
    inform(f"S3 connection OK: {bucket.name} (bucket: {bucket.bucket})")
    return True
