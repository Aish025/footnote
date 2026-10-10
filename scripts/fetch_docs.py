"""Fetch Kubernetes release notes (CHANGELOG per minor) and versioned docs into data/raw/.

Scope comes from data/manifest.yaml (release_notes, docs). No GitHub token needed:
- Release notes: raw.githubusercontent.com, pinned to one commit SHA (resolved with
  `git ls-remote`) so every CHANGELOG comes from the same snapshot.
  -> data/raw/release_notes/CHANGELOG-<version>.md + _meta.json
- Docs: shallow, sparse `git clone` of kubernetes/website per version branch, copying only
  content/en/docs. -> data/raw/docs/v<version>/... + _meta.json

Re-running skips anything whose recorded commit SHA still matches upstream (use --force to
re-fetch). Each _meta.json records repo, ref and the exact SHA for reproducibility.

Usage:
    python scripts/fetch_docs.py
    python scripts/fetch_docs.py --only release_notes
    python scripts/fetch_docs.py --only docs --force
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from corpus import (
    DOCS_DIR,
    RAW_DIR,
    RELEASE_NOTES_DIR,
    DocsSpec,
    ReleaseNotesSpec,
    load_manifest,
    write_json_atomic,
)
from settings import Settings, get_settings

log = logging.getLogger("fetch_docs")

META_FILE = "_meta.json"


def github_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def resolve_ref(repo: str, ref: str) -> str:
    """Commit SHA that branch `ref` points to right now (no API call, no rate limit)."""
    out = subprocess.run(
        ["git", "ls-remote", github_url(repo), f"refs/heads/{ref}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    if not out:
        raise ValueError(f"branch {ref!r} not found in {repo}")
    return out[0]


def recorded_sha(directory: Path) -> str | None:
    meta = directory / META_FILE
    if not meta.exists():
        return None
    return json.loads(meta.read_text(encoding="utf-8")).get("sha")


def looks_like_changelog(text: str, version: str) -> bool:
    """Guard against saving an error page: a real CHANGELOG has '# v<version>.' headings."""
    return f"# v{version}." in text


def fetch_release_notes(spec: ReleaseNotesSpec, settings: Settings, force: bool) -> None:
    sha = resolve_ref(spec.repo, spec.ref)
    if not force and recorded_sha(RELEASE_NOTES_DIR) == sha:
        log.info("release notes up to date at %s", sha[:12])
        return
    files = {}
    with httpx.Client(timeout=settings.request_timeout_s, follow_redirects=True) as http:
        for version in spec.versions:
            path = spec.path_template.format(version=version)
            resp = http.get(f"{settings.raw_content_url}/{spec.repo}/{sha}/{path}")
            resp.raise_for_status()
            if not looks_like_changelog(resp.text, version):
                raise ValueError(f"{path} at {sha[:12]} does not look like a changelog")
            target = RELEASE_NOTES_DIR / f"CHANGELOG-{version}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resp.content)
            files[version] = path
            log.info("saved %s (%s bytes)", target.name, f"{len(resp.content):,}")
    write_json_atomic(
        RELEASE_NOTES_DIR / META_FILE,
        {"repo": spec.repo, "ref": spec.ref, "sha": sha, "files": files, "fetched_at": _now()},
    )


def remove_tree(path: Path) -> None:
    """rmtree that also works on Windows, where git marks pack files read-only."""
    for p in path.rglob("*"):
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    shutil.rmtree(path)


def sparse_clone(repo: str, ref: str, subdir: str, dest: Path) -> str:
    """Shallow clone of one branch, checking out only `subdir`. Returns the HEAD SHA."""
    git = ["git", "-c", "core.longpaths=true"]
    subprocess.run(
        [*git, "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse"]
        + ["--branch", ref, github_url(repo), str(dest)],
        check=True,
    )
    subprocess.run([*git, "-C", str(dest), "sparse-checkout", "set", subdir], check=True)
    head = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return head.stdout.strip()


def fetch_docs_version(spec: DocsSpec, version: str, ref: str, force: bool) -> None:
    target = DOCS_DIR / f"v{version}"
    if not force and recorded_sha(target) == resolve_ref(spec.repo, ref):
        log.info("docs v%s up to date", version)
        return
    tmp_root = RAW_DIR / ".tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    clone_dir = Path(tempfile.mkdtemp(dir=tmp_root))
    try:
        log.info("cloning %s@%s (%s only)...", spec.repo, ref, spec.path)
        sha = sparse_clone(spec.repo, ref, spec.path, clone_dir / "repo")
        if target.exists():
            remove_tree(target)
        shutil.copytree(clone_dir / "repo" / spec.path, target)
    finally:
        remove_tree(clone_dir)
        if not any(tmp_root.iterdir()):
            tmp_root.rmdir()
    pages = sum(1 for _ in target.rglob("*.md"))
    write_json_atomic(
        target / META_FILE,
        {
            "repo": spec.repo,
            "ref": ref,
            "sha": sha,
            "path": spec.path,
            "markdown_pages": pages,
            "fetched_at": _now(),
        },
    )
    log.info("saved docs v%s: %d markdown pages at %s", version, pages, sha[:12])


def _now() -> str:
    return datetime.now(UTC).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=["release_notes", "docs"], help="fetch one part only")
    parser.add_argument("--force", action="store_true", help="re-fetch even if SHA unchanged")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is noise

    manifest = load_manifest()
    if args.only in (None, "release_notes"):
        fetch_release_notes(manifest.release_notes, get_settings(), args.force)
    if args.only in (None, "docs"):
        for version, ref in manifest.docs.versions.items():
            fetch_docs_version(manifest.docs, version, ref, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
