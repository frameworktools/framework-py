"""Portable artifact bundles and named publishing destinations."""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import tempfile
import warnings
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from framework.errors import FrameworkError, FrameworkWarning


def doc_title(path: str | Path) -> str | None:
    """The ``title`` in a Quarto document's YAML front matter, or None.

    A ``.qmd`` or ``.Rmd`` file's front matter opens the file. A Jupyter notebook's sits
    in its first cell, a raw cell, as Quarto reads it. A notebook whose first cell is a
    markdown cell starting with a level-1 heading (``# Title``, as Framework's notebook
    stub has) takes the heading, as Quarto does. Publishing uses the title in place of the
    file name, as R does.
    """
    import yaml

    source = Path(path)
    suffix = source.suffix.lower()
    if not source.is_file() or suffix not in (".qmd", ".rmd", ".ipynb"):
        return None
    try:
        text = source.read_text(encoding="utf-8")
        if suffix == ".ipynb":
            cells = json.loads(text).get("cells") or []
            first = cells[0] if cells else {}
            body = first.get("source", "")
            text = "".join(body) if isinstance(body, list) else str(body)
            if first.get("cell_type") == "markdown":
                heading = re.match(r"\A\s*#[ \t]+(.+?)[ \t#]*(?:\r?\n|\Z)", text)
                return heading.group(1).strip() if heading else None
            if first.get("cell_type") != "raw":
                return None
    except (OSError, UnicodeError, ValueError, AttributeError):
        return None
    match = re.match(r"\A\s*---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(\r?\n|\Z)", text, re.S)
    if match is None:
        return None
    try:
        front = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    title = front.get("title") if isinstance(front, dict) else None
    return title.strip() if isinstance(title, str) and title.strip() else None


def artifact_slug(value: str) -> str:
    """Lowercase a name and replace each non-alphanumeric run with a hyphen."""
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not result:
        raise FrameworkError("An artifact name needs a letter or number.")
    return result


# Folder layouts: every publish kept under <slug>/v/<n>/ with a site index, or the latest
# publish alone at the folder's root, as a GitHub Pages docs/ folder wants
LAYOUTS = ("versioned", "site-root")
SITE_RECORD = ".framework-site.json"
# Sources a site-root publish never writes: only their rendered output goes to the site
_NOTEBOOK_SOURCES = (".qmd", ".ipynb", ".rmd")


@dataclass(frozen=True)
class Destination:
    name: str
    driver: str
    options: dict[str, Any]


def destination(to: str | None, connection: str | None = None) -> Destination | None:
    """Resolve explicit names, the project default, then the legacy destination."""
    from framework.settings import settings_read

    config = settings_read()
    publish = config.get("publish") or {}
    if not isinstance(publish, dict):
        raise FrameworkError("publish settings must be a mapping.")
    choices = publish.get("destinations") or {}
    if not isinstance(choices, dict):
        raise FrameworkError("publish.destinations must be a mapping.")
    selected = to or (None if connection is not None else publish.get("default"))
    if selected is not None and not isinstance(selected, str):
        raise FrameworkError("The publishing destination must be a name.")
    if selected is None:
        return None  # Existing bucket/cloud behavior remains compatible.
    if selected in choices:
        options = choices[selected]
        if not isinstance(options, dict):
            raise FrameworkError(f"Invalid publishing destination: {selected}")
        driver = options.get("driver")
    else:
        driver, options = selected, {}
    driver = "s3" if driver == "bucket" else driver
    if driver not in ("cloud", "github", "folder", "s3"):
        raise FrameworkError(
            'to must be "cloud", "bucket", "github", or None, or a configured destination '
            f"name (got {selected})."
        )
    options = dict(options)
    if "private" in options:
        if not isinstance(options["private"], bool):
            raise FrameworkError("Destination private must be true or false.")
        options.setdefault("visibility", "private" if options["private"] else "public")
    return Destination(str(selected), driver, options)


def validate(
    destination: Destination,
    visibility: str | None,
    password: str | None,
    collection: str | int | None,
) -> None:
    """Reject unsupported access controls before rendering or delivery."""
    if visibility not in (None, "public", "private", "unlisted"):
        raise FrameworkError("visibility must be public, private, or unlisted.")
    if destination.driver == "cloud":
        return
    if password is not None:
        raise FrameworkError(f"{destination.driver} does not support artifact passwords.")
    if collection is not None:
        raise FrameworkError("Collections require publishing to Framework Cloud.")
    if visibility == "unlisted":
        raise FrameworkError(f"{destination.driver} does not support unlisted artifacts.")
    if destination.driver == "github" and visibility == "private":
        # GitHub repository privacy does not establish Pages access control.
        from framework._pages import private_pages

        if not private_pages():
            raise FrameworkError("GitHub has not confirmed private Pages access for this project.")
    layout = destination.options.get("layout")
    if layout is not None:
        if destination.driver != "folder":
            raise FrameworkError("layout applies to folder destinations only.")
        if layout not in LAYOUTS:
            raise FrameworkError(f"A folder destination's layout must be {' or '.join(LAYOUTS)}.")
    if destination.driver == "folder":
        if not destination.options.get("path"):
            raise FrameworkError("A folder destination needs a path in publish.destinations.")
        if visibility is not None and destination.options.get("visibility") != visibility:
            raise FrameworkError("Folder visibility must match its configured share visibility.")
    if (
        destination.driver == "s3"
        and visibility == "private"
        and (destination.options.get("visibility") != "private" or destination.options.get("url"))
    ):
        raise FrameworkError("Private S3 publishing needs a destination configured as private.")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _reader(manifest: dict[str, Any], representation: dict[str, Any] | None) -> str:
    template = Path(__file__).with_name("templates") / "reader.html"
    if not template.is_file():
        raise FrameworkError("The static artifact reader is missing from this installation.")
    payload = _json({"manifest": manifest, "representation": representation})
    for source, escaped in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        payload = payload.replace(source, escaped)
    values = {
        "__FRAMEWORK_TITLE__": html.escape(manifest["title"], quote=True),
        "__FRAMEWORK_ARTIFACT__": payload,
    }
    return re.sub(
        r"__FRAMEWORK_(?:TITLE|ARTIFACT)__",
        lambda match: values[match[0]],
        template.read_text(encoding="utf-8"),
    )


def build_bundle(
    source: Any,
    target: Path,
    *,
    slug: str | None = None,
    title: str | None = None,
    description: str | None = None,
    kind: str | None = None,
    self_contained: bool = True,
) -> dict[str, Any]:
    """Build a self-contained folder with checksums and an offline reader."""
    from framework import __version__, _artifacts
    from framework._pages import _stage
    from framework.storage import _artifact_file

    target.mkdir(parents=True, exist_ok=True)
    representation = None
    original = None
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FrameworkError(f"Source not found: {path}")
        name = path.name if path.is_dir() else path.stem
        if path.is_symlink() or (path.is_dir() and any(p.is_symlink() for p in path.rglob("*"))):
            raise FrameworkError("Publish bundles cannot contain symbolic links.")
        if path.is_dir() or path.suffix.lower() in (".html", ".htm", ".qmd", ".ipynb"):
            if path.is_dir() and not (path / "index.html").is_file():
                shutil.copytree(path, target, dirs_exist_ok=True)
                links = "".join(
                    f'<li><a href="{quote(p.relative_to(target).as_posix())}">'
                    f"{html.escape(p.relative_to(target).as_posix())}</a></li>"
                    for p in sorted(target.rglob("*"))
                    if p.is_file()
                )
                (target / "index.html").write_text(
                    '<!doctype html><meta charset="utf-8"><title>Files</title><ul>'
                    + links
                    + "</ul>",
                    encoding="utf-8",
                )
            else:
                _stage(path, target, self_contained)
            if path.is_file():
                # Keep the rendered source under its own name, including index.html.
                if path.suffix.lower() in (".html", ".htm"):
                    original = path.name
                    if original != "index.html":
                        shutil.copy2(path, target / original)
                else:
                    original = path.name
                    shutil.copy2(path, target / original)
            kind = kind or "html"
        else:
            original = path.name
            if original in ("artifact.json", "representation.json", "index.html"):
                original = "original-" + original
            shutil.copy2(path, target / original)
            if path.suffix.lower() == ".json":
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(document, dict) and document.get("schema") == _artifacts.SCHEMA:
                        representation = document
                except (ValueError, UnicodeError):
                    pass
            kind = (
                kind
                or (representation or {}).get("kind")
                or (
                    "image"
                    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp")
                    else "file"
                )
            )
    else:
        representation = _artifacts.structured(source)
        path = _artifact_file(source, target, cloud=True)
        original = path.name
        name = str(kind or (representation or {}).get("kind") or path.stem)
        kind = (
            kind
            or (representation or {}).get("kind")
            or ("image" if path.suffix.lower() in (".png", ".svg") else "html")
        )
        frame = _artifacts.as_frame(source)
        if frame is not None:
            _artifacts.frame_for_export(frame).to_csv(target / "data.csv", index=False)
    manifest: dict[str, Any] = {
        "schema": "framework.bundle/v1",
        "slug": artifact_slug(slug or name),
        "title": title or (doc_title(source) if isinstance(source, (str, Path)) else None) or name,
        "description": description,
        "kind": kind,
        "entrypoint": "index.html",
        "representation": None,
        "original": original,
        "source": (representation or {}).get("source", {"language": "python"}),
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "generator": f"framework-py {__version__}",
    }
    if representation is not None:
        manifest["representation"] = "representation.json"
        (target / "representation.json").write_text(_json(representation), encoding="utf-8")
    if not (target / "index.html").exists():
        if representation is None and kind == "html" and original:
            shutil.copy2(target / original, target / "index.html")
        else:
            (target / "index.html").write_text(_reader(manifest, representation), encoding="utf-8")
    manifest["files"] = [
        {
            "path": p.relative_to(target).as_posix(),
            "bytes": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(target.rglob("*"))
        if p.is_file() and p != target / "artifact.json"
    ]
    (target / "artifact.json").write_text(_json(manifest), encoding="utf-8")
    return manifest


def folder_path(path: str | Path) -> Path:
    """A folder destination's path: ``~`` is the home folder, and a relative path starts
    at the project root, wherever Python runs (a notebook renders from ``notebooks/``).
    Outside a project, a relative path starts at the current folder."""
    from framework._paths import in_root
    from framework.project import find_settings_file

    found = find_settings_file()
    return in_root(path, found.parent if found is not None else Path.cwd())


def folder_deliver(bundle: Path, site: Path, base_url: str | None = None) -> dict[str, Any]:
    """Keep immutable snapshots and rebuild a relative-link site index."""
    manifest = json.loads((bundle / "artifact.json").read_text(encoding="utf-8"))
    site.mkdir(parents=True, exist_ok=True)
    target = site / manifest["slug"]
    if target.is_symlink() or (target / "v").is_symlink():
        raise FrameworkError("The publishing destination cannot be a symbolic link.")
    if (bundle / "v").exists():
        raise FrameworkError("The bundle folder name v is reserved for version archives.")
    versions = target / "v"
    versions.mkdir(parents=True, exist_ok=True)
    version = max((int(p.name) for p in versions.iterdir() if p.name.isdigit()), default=0) + 1
    archive = versions / str(version)
    shutil.copytree(bundle, archive)
    for item in target.iterdir():
        if item.name != "v":
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    for item in bundle.iterdir():
        if item.is_dir():
            shutil.copytree(item, target / item.name)
        else:
            shutil.copy2(item, target / item.name)
    entries = []
    for file in sorted(site.glob("*/artifact.json")):
        entry = json.loads(file.read_text(encoding="utf-8"))
        entries.append({key: entry.get(key) for key in ("slug", "title", "kind", "description")})
    (site / "index.json").write_text(_json(entries), encoding="utf-8")
    rows = "".join(
        f'<li><a href="{quote(e["slug"])}/">{html.escape(e["title"])}</a></li>' for e in entries
    )
    (site / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1"><title>Artifacts</title>'
        "<style>body{font:16px system-ui;max-width:60rem;margin:3rem auto;padding:0 1rem}"
        "li{margin:1rem 0}</style><h1>Artifacts</h1><ul>" + rows + "</ul></html>",
        encoding="utf-8",
    )
    url = f"{base_url.rstrip('/')}/{manifest['slug']}/" if base_url else None
    return {
        "driver": "folder",
        "url": url,
        "version": version,
        "version_url": f"{url}v/{version}/" if url else None,
        "location": str(target),
    }


def folder_deliver_root(bundle: Path, site: Path, base_url: str | None = None) -> dict[str, Any]:
    """Put the latest publish alone at the folder's root, with no versions or index.

    The files the previous publish wrote, listed in ``.framework-site.json``, are removed
    first; other files in the folder (``CNAME``, ``.nojekyll``) are left alone. Only
    rendered output is written: the bundle's ``artifact.json`` isn't, nor a notebook's
    source (``.qmd``, ``.ipynb``, ``.Rmd``), nor a copy of an HTML source identical to
    ``index.html``, so a deck is written once.
    """
    manifest = json.loads((bundle / "artifact.json").read_text(encoding="utf-8"))
    if site.is_symlink():
        raise FrameworkError("The publishing destination cannot be a symbolic link.")
    site.mkdir(parents=True, exist_ok=True)
    record_path = site / SITE_RECORD
    try:
        previous = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    previous = previous if isinstance(previous, dict) else {}
    index = (bundle / "index.html").read_bytes() if (bundle / "index.html").is_file() else None
    original = manifest.get("original")

    def source_copy(path: Path) -> bool:
        """The bundle's copy of a notebook source, or of an HTML source equal to index.html."""
        if path.parent != bundle or path.name != original or path.name == "index.html":
            return False
        suffix = path.suffix.lower()
        if suffix in _NOTEBOOK_SOURCES:
            return True
        return suffix in (".html", ".htm") and path.read_bytes() == index

    files = [
        path
        for path in sorted(bundle.rglob("*"))
        if path.is_file() and path.name != "artifact.json" and not source_copy(path)
    ]
    root = site.resolve()
    for relative in previous.get("files") or []:
        old = (site / str(relative)).resolve()
        if old.is_relative_to(root) and old != root and old.is_file():
            old.unlink()
            for folder in old.parents:
                if folder == root or any(folder.iterdir()):
                    break
                folder.rmdir()
    written = []
    for path in files:
        relative = path.relative_to(bundle)
        target = site / relative
        if target.is_symlink():
            raise FrameworkError(f"The publishing destination has a symbolic link: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        written.append(relative.as_posix())
    # Versions count per slug: a different artifact in the folder starts again at 1
    same_slug = previous.get("slug") == manifest.get("slug")
    version = int(previous.get("version") or 0) + 1 if same_slug else 1
    record = {
        "schema": "framework.site/v1",
        "version": version,
        **{key: manifest.get(key) for key in ("slug", "title", "kind", "description")},
        "created_at": manifest.get("created_at"),
        "files": written,
    }
    record_path.write_text(_json(record), encoding="utf-8")
    return {
        "driver": "folder",
        "url": f"{base_url.rstrip('/')}/" if base_url else None,
        "version": version,
        "version_url": None,
        "location": str(site / "index.html"),
    }


def track_external(
    manifest: dict[str, Any], receipt: dict[str, Any], bundle: Path, visibility: str | None
) -> None:
    """Record metadata after delivery. Local paths and artifact contents stay local."""
    from framework.cloud import api, cloud_server, project_token

    token = project_token()
    if token is None:
        return
    try:
        server = cloud_server(scope="project").url
        me = api("/api/v1/me", token, server)
        project = me.get("project") or {}
        slug = (project.get("slug") if isinstance(project, dict) else project) or me.get("slug")
        if not slug:
            raise FrameworkError("The project token did not identify a project slug.")
        payload = {
            key: manifest.get(key) for key in ("slug", "title", "kind", "description", "source")
        }
        payload.update(
            {key: receipt.get(key) for key in ("driver", "url", "version", "version_url")}
        )
        payload.update(
            size_bytes=receipt.get(
                "size_bytes", sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
            ),
            sha256=receipt.get(
                "sha256", hashlib.sha256((bundle / "artifact.json").read_bytes()).hexdigest()
            ),
            published_at=manifest["created_at"],
            visibility=visibility or "public",
        )
        api(
            f"/api/v1/projects/{quote(str(slug), safe='')}/artifacts/external",
            token,
            server,
            method="POST",
            body=payload,
        )
    except Exception as error:
        warnings.warn(
            f"Published successfully, but cloud tracking failed: {error}",
            FrameworkWarning,
            stacklevel=3,
        )


def publish_external(
    source: Any,
    selected: Destination,
    *,
    slug: str | None = None,
    title: str | None = None,
    description: str | None = None,
    kind: str | None = None,
    visibility: str | None = None,
    self_contained: bool = True,
    connection: str | None = None,
    built_bundle: Path | None = None,
    built_manifest: dict[str, Any] | None = None,
) -> str:
    context = (
        tempfile.TemporaryDirectory(prefix="fw_bundle_")
        if built_bundle is None
        else nullcontext(str(built_bundle.parent))
    )
    with context as temp:
        bundle = built_bundle or Path(temp) / "bundle"
        manifest = built_manifest or build_bundle(
            source,
            bundle,
            slug=slug,
            title=title,
            description=description,
            kind=kind,
            self_contained=self_contained,
        )
        if selected.driver == "folder":
            deliver = (
                folder_deliver_root
                if selected.options.get("layout") == "site-root"
                else folder_deliver
            )
            receipt = deliver(
                bundle, folder_path(selected.options["path"]), selected.options.get("url")
            )
        elif selected.driver == "github":
            from framework._pages import github_publish

            url = github_publish(
                bundle,
                slug=manifest["slug"],
                bundle=True,
                private=(visibility or selected.options.get("visibility")) == "private",
            )
            receipt = {**_github_receipt(bundle), "driver": "github", "url": url}
        else:
            receipt = _s3_deliver(
                bundle, selected, visibility or selected.options.get("visibility"), connection
            )
        track_external(manifest, receipt, bundle, visibility or selected.options.get("visibility"))
        if selected.driver != "github":  # The GitHub adapter reports its own URL
            _report(receipt)
        return str(receipt["url"] or receipt["location"])


def _report(receipt: dict[str, Any]) -> None:
    """Say where a bundle went, as the other publishing paths do."""
    from framework._log import inform

    inform(f"Published version {receipt['version']}: {receipt['location']}")
    if receipt.get("url"):
        inform(f"  URL: {receipt['url']}")


def _github_receipt(bundle: Path) -> dict[str, Any]:
    # The GitHub adapter writes its receipt next to the temporary source, outside the bundle.
    result: dict[str, Any] = json.loads(
        (bundle.parent / "receipt.json").read_text(encoding="utf-8")
    )
    return result


def _s3_deliver(
    bundle: Path, selected: Destination, visibility: str | None, connection: str | None
) -> dict[str, Any]:
    from framework.storage import _build, _content_type, resolve_bucket

    options = selected.options
    bucket = (
        _build(selected.name, options)
        if "region" in options or "endpoint" in options
        else resolve_bucket(
            str(options.get("bucket") or connection)
            if options.get("bucket") or connection
            else None
        )
    )
    client = bucket.client()
    if visibility == "private":
        try:
            status = client.get_bucket_policy_status(Bucket=bucket.bucket)
            if status.get("PolicyStatus", {}).get("IsPublic") is not False:
                raise FrameworkError("The S3 bucket policy allows public access.")
        except Exception as error:
            raise FrameworkError(
                "Private S3 access could not be verified. Nothing was uploaded."
            ) from error
    manifest = json.loads((bundle / "artifact.json").read_text(encoding="utf-8"))
    slug = manifest["slug"]
    prefix = bucket.key(f"{slug}/v/")
    version = 1
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket.bucket, Prefix=prefix):
        for entry in page.get("Contents", []):
            number = entry["Key"][len(prefix) :].split("/")[0]
            if number.isdigit():
                version = max(version, int(number) + 1)
    for path in sorted(bundle.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(bundle).as_posix()
        for name in (f"{slug}/v/{version}/{relative}", f"{slug}/{relative}"):
            extra = {
                "ContentType": _content_type(path),
                "ACL": "private" if visibility == "private" else "public-read",
            }
            try:
                client.upload_file(str(path), bucket.bucket, bucket.key(name), ExtraArgs=extra)
            except Exception as error:
                if "AccessControlListNotSupported" not in str(error):
                    raise FrameworkError(f"Failed to upload to S3: {error}") from error
                del extra["ACL"]
                client.upload_file(str(path), bucket.bucket, bucket.key(name), ExtraArgs=extra)
    entries: list[dict[str, Any]] = []
    try:
        previous = client.get_object(Bucket=bucket.bucket, Key=bucket.key("index.json"))
        entries = json.loads(previous["Body"].read())
    except Exception as error:
        code = getattr(error, "response", {}).get("Error", {}).get("Code")
        if code not in ("NoSuchKey", "404", "NotFound"):
            raise FrameworkError(
                f"Artifact uploaded, but the S3 index could not be read: {error}"
            ) from error
    # The index is one shared object, and destinations can share a bucket and prefix.
    # Its access follows the destination, not this publish. A public-read index lists
    # only entries recorded as public, so older entries without a visibility drop out
    # until they are published again.
    index_private = options.get("visibility") == "private"
    entries = [entry for entry in entries if entry.get("slug") != slug]
    added = {key: manifest.get(key) for key in ("slug", "title", "kind", "description")}
    entries.append({**added, "visibility": "private" if visibility == "private" else "public"})
    if not index_private:
        entries = [entry for entry in entries if entry.get("visibility") == "public"]
    entries.sort(key=lambda entry: entry["slug"])
    rows = "".join(
        f'<li><a href="{quote(entry["slug"])}/index.html">{html.escape(entry["title"])}</a></li>'
        for entry in entries
    )
    for name, raw, content_type in (
        ("index.json", _json(entries), "application/json"),
        (
            "index.html",
            '<!doctype html><meta charset="utf-8"><title>Artifacts</title>'
            "<h1>Artifacts</h1><ul>" + rows + "</ul>",
            "text/html",
        ),
    ):
        extra = {"ACL": "private" if index_private else "public-read"}
        try:
            client.put_object(
                Bucket=bucket.bucket,
                Key=bucket.key(name),
                Body=raw.encode("utf-8"),
                ContentType=content_type,
                **extra,
            )
        except Exception as error:
            if "AccessControlListNotSupported" not in str(error):
                raise FrameworkError(f"Failed to upload S3 index: {error}") from error
            client.put_object(
                Bucket=bucket.bucket,
                Key=bucket.key(name),
                Body=raw.encode("utf-8"),
                ContentType=content_type,
            )
    url = (
        None
        if visibility == "private"
        else (
            f"{options['url'].rstrip('/')}/{slug}/"
            if options.get("url")
            else bucket.url(bucket.key(f"{slug}/index.html"))
        )
    )
    return {
        "driver": "s3",
        "url": url,
        "version": version,
        "version_url": (url.removesuffix("index.html") + f"v/{version}/index.html")
        if url
        else None,
        "location": f"s3://{bucket.bucket}/{bucket.key(slug)}",
    }
