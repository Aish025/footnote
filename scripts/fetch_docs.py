"""Download every source document listed in docs/sources.csv into docs/raw/.

docs/raw/ is gitignored, so this script is how anyone rebuilds the corpus from the
manifest. It enforces CLAUDE.md hard rule 7 (official sources only) before downloading.

Usage:
    python scripts/fetch_docs.py                 # download missing files
    python scripts/fetch_docs.py --force         # re-download everything
    python scripts/fetch_docs.py --only indigo_coc akasa_baggage
    python scripts/fetch_docs.py --out-dir /tmp/raw   # dry run somewhere else
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import urllib.request
from datetime import date
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_CSV = REPO_ROOT / "docs" / "sources.csv"
RAW_DIR = REPO_ROOT / "docs" / "raw"

# Hard rule 7: dgca.gov.in and the airlines' own domains. Subdomains are allowed.
OFFICIAL_DOMAINS = ("dgca.gov.in", "goindigo.in", "airindia.com", "akasaair.com")

# Honest UA naming the tool; some sites reject the default Python one.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FootnoteCorpusFetcher/0.1"
TIMEOUT_SECONDS = 60


class Authority(StrEnum):
    REGULATOR = "regulator"
    AIRLINE = "airline"


class DocFormat(StrEnum):
    PDF = "pdf"
    HTML = "html"


class Source(BaseModel):
    """One row of docs/sources.csv, validated."""

    doc_id: str
    title: str
    source_url: str
    format: DocFormat
    authority: Authority
    effective_date: date | None = None
    downloaded_on: date | None = None

    @field_validator("effective_date", "downloaded_on", mode="before")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("source_url")
    @classmethod
    def must_be_official(cls, url: str) -> str:
        if not is_official_url(url):
            raise ValueError(f"not an official source (hard rule 7): {url}")
        return url

    @property
    def filename(self) -> str:
        return f"{self.doc_id}.{self.format.value}"


def is_official_url(url: str) -> bool:
    """True if the URL is https and its host is an official domain or a subdomain of one."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return False
    return any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS)


def load_sources(csv_path: Path) -> list[Source]:
    """Read and validate every row of the sources manifest. Fails on the first bad row."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        sources = [Source(**row) for row in csv.DictReader(f)]
    ids = [s.doc_id for s in sources]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate doc_id in {csv_path.name}: {sorted(duplicates)}")
    return sources


def looks_like(content: bytes, fmt: DocFormat) -> bool:
    """Cheap sanity check that we got the document, not an error or bot-block page."""
    if fmt is DocFormat.PDF:
        return content.startswith(b"%PDF")
    head = content[:2048].lower()
    return b"<html" in head or b"<!doctype html" in head


def download(url: str) -> bytes:
    """GET a URL and return the body. Raises on HTTP errors and timeouts."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def fetch_one(source: Source, out_dir: Path, force: bool) -> str:
    """Download one source to out_dir. Returns a one-line status for the console."""
    target = out_dir / source.filename
    if target.exists() and not force:
        return f"skip   {source.filename} (exists; use --force to re-download)"
    content = download(source.source_url)
    if not looks_like(content, source.format):
        raise ValueError(f"{source.doc_id}: response is not a {source.format.value} document")
    target.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()[:12]
    return f"saved  {source.filename} ({len(content):,} bytes, sha256 {sha})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="re-download existing files")
    parser.add_argument("--only", nargs="+", metavar="DOC_ID", help="fetch only these doc_ids")
    parser.add_argument("--out-dir", type=Path, default=RAW_DIR, help="where to save files")
    args = parser.parse_args(argv)

    sources = load_sources(SOURCES_CSV)
    if args.only:
        unknown = set(args.only) - {s.doc_id for s in sources}
        if unknown:
            parser.error(f"unknown doc_id(s): {sorted(unknown)}")
        sources = [s for s in sources if s.doc_id in args.only]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for source in sources:
        try:
            print(fetch_one(source, args.out_dir, args.force))
        except Exception as exc:  # report every failure, then exit non-zero
            failures += 1
            print(f"FAILED {source.filename}: {exc}", file=sys.stderr)

    if failures:
        # IndiGo and Air India sit behind bot protection that stalls scripted clients.
        # We do not try to evade it: open the source_url in a browser, save the page
        # (or PDF) as docs/raw/<doc_id>.<format>, and re-run; existing files are skipped.
        print(
            "\nSome downloads failed. Save those pages manually from a browser to "
            f"{args.out_dir}/<doc_id>.<format>, then re-run.",
            file=sys.stderr,
        )
    print(
        f"\n{len(sources) - failures}/{len(sources)} ok. "
        "If you re-downloaded, update downloaded_on in docs/sources.csv."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
