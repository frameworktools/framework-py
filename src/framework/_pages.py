"""Publishing to GitHub Pages: ``publish(..., to="github")``.

Framework Cloud's "Publish with GitHub Pages" commits a workflow that deploys
the repository's ``published/`` folder from the default branch. This side
copies a document into ``published/<slug>/``, commits only that folder, and
pushes. Documents never pass through the cloud. R and fw behave the same way.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

from framework._log import inform
from framework._paths import rendered_page
from framework.errors import FrameworkError, FrameworkWarning

PUBLIC_NOTICE = "GitHub Pages sites are public, even when the repository is private."
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_REMOTE = re.compile(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$")


@dataclass(frozen=True)
class _Remote:
    name: str
    owner: str
    repo: str
    has_upstream: bool


def _git(top: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(top), *args], capture_output=True, text=True, check=False
    )


def github_remote(url: str) -> tuple[str, str] | None:
    """The owner and repository of a GitHub remote URL (https, ssh, or scp-style)."""
    match = _REMOTE.search(url.strip())
    return (match[1], match[2]) if match else None


def pages_base(owner: str, repo: str) -> str:
    """The URL GitHub gives a repository's Pages site."""
    host = f"{owner.lower()}.github.io"
    if repo.lower() == host:
        return f"https://{host}/"
    return f"https://{host}/{repo}/"


def pages_slug(source: Path) -> str:
    """The file name lowercased, with each run of other characters as "-".

    GitHub Pages URLs are case-sensitive. "Final Report (v2).html" becomes
    "final-report-v2".
    """
    name = source.name if source.is_dir() else source.stem
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _remote(top: Path) -> _Remote | None:
    """The branch's upstream remote, then origin, then any GitHub remote."""
    upstream = _git(top, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    upstream_name = upstream.stdout.strip().split("/")[0] if upstream.returncode == 0 else None
    listed = _git(top, "remote")
    names = listed.stdout.split() if listed.returncode == 0 else []
    candidates = [n for n in (upstream_name, "origin", *names) if n is not None]
    for name in dict.fromkeys(candidates):
        if name not in names:
            continue
        url = _git(top, "remote", "get-url", name)
        found = github_remote(url.stdout) if url.returncode == 0 else None
        if found is not None:
            return _Remote(name, found[0], found[1], name == upstream_name)
    return None


def _stage(source: Path, stage: Path, self_contained: bool) -> None:
    """Copy ``source`` into ``stage`` as a folder whose front page is index.html."""
    suffix = source.suffix.lower()
    if source.is_dir():
        if not (source / "index.html").is_file():
            raise FrameworkError(
                f"A folder published to GitHub Pages needs an index.html at its top: {source}"
            )
        shutil.copytree(source, stage, dirs_exist_ok=True)
        return
    if suffix in (".html", ".htm"):
        shutil.copy2(source, stage / source.name)
        assets = source.with_name(f"{source.stem}_files")
        if assets.is_dir():
            # The supporting folder keeps its name, so relative links still resolve
            shutil.copytree(assets, stage / assets.name)
    elif suffix in (".qmd", ".ipynb"):
        quarto = shutil.which("quarto")
        if quarto is None:
            raise FrameworkError("Rendering requires the quarto command on your PATH.")
        with tempfile.TemporaryDirectory(prefix="fw_render_") as render:
            out = Path(render)
            command = [quarto, "render", str(source.resolve()), "--output-dir", str(out)]
            command += ["--to", "html", *(["--embed-resources"] if self_contained else [])]
            inform(f"Rendering {source.name}...")
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise FrameworkError(f"Quarto render failed:\n{result.stdout}{result.stderr}")
            # A project's _quarto.yml keeps the notebook's folder under the output folder
            page = rendered_page(out, source.stem)
            if page is None:
                raise FrameworkError(f"No single HTML page found to publish from {source}")
            shutil.copytree(page.parent, stage, dirs_exist_ok=True)
    else:
        raise FrameworkError(
            "GitHub Pages publishing takes rendered HTML, a render folder, or a .qmd or "
            f".ipynb to render. Got: {suffix or source.name}"
        )
    if not (stage / "index.html").is_file():
        main = stage / f"{source.stem}.html"
        if not main.is_file():
            pages = sorted(stage.glob("*.htm*"))
            if len(pages) != 1:
                raise FrameworkError(f"No single HTML page found to publish from {source}")
            main = pages[0]
        main.rename(stage / "index.html")


def _cloud_pages_url(root: Path) -> tuple[bool, str | None, str | None]:
    """Whether the cloud answered, its Pages URL (None when Pages is off), and the server.

    One call to ``/api/v1/me`` with the project token. Not known when there is
    no project token or the call fails.
    """
    from framework.cloud import CloudError, api, cloud_server, project_token

    token = project_token(root)
    if token is None:
        return False, None, None
    try:
        server = cloud_server(scope="project", project_dir=root).url
        me = api("/api/v1/me", token, server)
    except CloudError:
        return False, None, None
    if not isinstance(me, dict) or me.get("kind") != "project":
        return False, None, None
    pages = me.get("pages_url")
    return True, pages if isinstance(pages, str) and pages else None, server


def _confirm() -> None:
    # Shown on every run, even when Framework's messages are off
    print(PUBLIC_NOTICE, file=sys.stderr)
    if sys.stdin.isatty():
        answer = input("Publish it? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            raise FrameworkError("Cancelled: nothing was published.")


def _directory_bytes(folder: Path) -> int:
    """All files in a site, including retained artifact versions."""
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def _check_pages_budget(site: Path, incoming: Path, *, bundle: bool) -> None:
    # Bundles are copied to both the current path and an immutable version folder.
    # Keep existing files in the estimate, including every retained archive.
    projected = _directory_bytes(site) + _directory_bytes(incoming) * (2 if bundle else 1)
    if projected > 1_000_000_000:
        raise FrameworkError("Publishing would exceed the 1 GB GitHub Pages site limit.")
    if projected > 800_000_000:
        warnings.warn(
            "The GitHub Pages site is approaching its 1 GB limit.",
            FrameworkWarning,
            stacklevel=3,
        )


def github_publish(
    source: Path,
    slug: str | None = None,
    self_contained: bool = True,
    *,
    bundle: bool = False,
    private: bool = False,
) -> str:
    """Copy ``source`` to ``published/<slug>/``, commit only that folder, and push."""
    from framework.project import find_settings_file

    if slug is None:
        slug = pages_slug(source)
    if not _SLUG.match(slug):
        raise FrameworkError(
            "slug must start with a lowercase letter or digit and use only lowercase letters, "
            "digits, '.', '_', and '-' (GitHub Pages URLs are case-sensitive)."
        )
    settings_file = find_settings_file()
    root = settings_file.parent if settings_file is not None else Path.cwd()
    shown = _git(root, "rev-parse", "--show-toplevel")
    if shown.returncode != 0:
        raise FrameworkError(
            "This project isn't a git repository. Create the project on GitHub first."
        )
    top = Path(shown.stdout.strip())
    remote = _remote(top)
    if remote is None:
        raise FrameworkError(
            "This project has no GitHub remote. Create the project on GitHub first, then "
            "publish again."
        )
    if not private:
        _confirm()

    # published/ sits at the top of the repository, where the workflow deploys from
    rel = f"published/{slug}"
    with tempfile.TemporaryDirectory(prefix="fw_pages_") as temp:
        _stage(source, Path(temp), self_contained)
        target = top / rel
        _check_pages_budget(top / "published", source if bundle else Path(temp), bundle=bundle)
        if bundle:
            import json

            from framework.publish_bundle import folder_deliver

            receipt = folder_deliver(source, top / "published")
            (source.parent / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        else:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(temp, target)

    # Include the regenerated site index in the final check before staging or pushing.
    if _directory_bytes(top / "published") > 1_000_000_000:
        raise FrameworkError("Publishing would exceed the 1 GB GitHub Pages site limit.")

    # Commit only this folder; anything else the user staged stays staged
    paths = [rel, "published/index.html", "published/index.json"] if bundle else [rel]
    _git(top, "add", "-A", "--", *paths)
    if _git(top, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        inform(f"No change in {rel}; nothing committed or pushed.")
    else:
        commit = _git(top, "commit", "-m", f"Publish {slug}", "--", *paths)
        if commit.returncode != 0:
            raise FrameworkError(f"git commit failed:\n{commit.stdout}{commit.stderr}")
        branch = _git(top, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        default = _git(top, "symbolic-ref", "--short", f"refs/remotes/{remote.name}/HEAD")
        if default.returncode == 0:
            default_branch = default.stdout.strip().split("/", 1)[-1]
            if branch != default_branch:
                warnings.warn(
                    f"You're on '{branch}'. GitHub Pages deploys only from the default branch "
                    f"('{default_branch}'), so this won't appear until it's merged there.",
                    FrameworkWarning,
                    stacklevel=3,
                )
        push = (
            _git(top, "push")
            if remote.has_upstream
            else _git(top, "push", "-u", remote.name, "HEAD")
        )
        if push.returncode != 0:
            raise FrameworkError(f"Committed, but git push failed:\n{push.stdout}{push.stderr}")

    known, pages_url, server = _cloud_pages_url(root)
    if known and pages_url is None:
        warnings.warn(
            f"GitHub Pages is off for this project. Turn it on under Projects at {server}.",
            FrameworkWarning,
            stacklevel=3,
        )
    base = pages_url or pages_base(remote.owner, remote.repo)
    url = f"{base.rstrip('/')}/{slug}/"
    if bundle:
        import json

        receipt_path = source.parent / "receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt.update(
            driver="github",
            url=url,
            version_url=f"{url}v/{receipt['version']}/",
            commit=_git(top, "rev-parse", "HEAD").stdout.strip(),
        )
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    inform(f"Published to {url}")
    return url


def private_pages() -> bool:
    """Ask GitHub for the Pages access setting; repository privacy is insufficient."""
    import json

    from framework.project import project_root

    top = project_root()
    remote = _remote(top)
    if remote is None:
        return False
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{remote.owner}/{remote.repo}/pages"],
            capture_output=True,
            text=True,
            check=False,
            cwd=top,
        )
        return result.returncode == 0 and json.loads(result.stdout).get("public") is False
    except (OSError, ValueError):
        return False
