"""Download the Amazon Reviews 2023 files listed in data/manifest.yaml into data/raw/.

- Streams to `<file>.part` and resumes with an HTTP Range request after any interruption
  (Ctrl-C, network drop): re-running continues where it stopped.
- Refuses a file whose final size differs from the manifest's `bytes`.
- Records sha256, size, URL and date per file in data/raw/_downloads.json.

Usage:
    python scripts/download_data.py                      # all files
    python scripts/download_data.py --only meta qa_2016 qa_2019
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from manifest import DOWNLOADS_RECORD, FileSpec, load_manifest

log = logging.getLogger("download_data")

CHUNK_BYTES = 1 << 20  # 1 MiB per read
PROGRESS_EVERY_BYTES = 200 << 20  # log every 200 MiB
TIMEOUT_S = 60.0
MAX_RETRIES = 8
BACKOFF_BASE_S = 2.0


def range_header(existing_bytes: int) -> dict[str, str]:
    """Header to resume a partial download (empty when starting from zero)."""
    return {"Range": f"bytes={existing_bytes}-"} if existing_bytes else {}


class RetryableDownloadError(RuntimeError):
    """Server answered with something other than 200/206 (e.g. 503); retry with resume."""


def resume_offset(status: int, requested_offset: int) -> int:
    """Where the body we receive starts: 206 honours our Range; 200 means start over."""
    if status == 206:
        return requested_offset
    if status == 200:
        return 0
    raise RetryableDownloadError(f"unexpected HTTP {status}")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK_BYTES * 8):
            digest.update(block)
    return digest.hexdigest()


def stream_once(client: httpx.Client, spec: FileSpec, part: Path) -> None:
    """One attempt: append the remaining bytes to `part` (raises on network errors)."""
    offset = part.stat().st_size if part.exists() else 0
    with client.stream("GET", spec.url, headers=range_header(offset)) as resp:
        if resp.status_code == 416:  # nothing left to fetch
            return
        start = resume_offset(resp.status_code, offset)
        mode = "ab" if start else "wb"
        written, next_log = start, start + PROGRESS_EVERY_BYTES
        with part.open(mode) as f:
            for chunk in resp.iter_bytes(CHUNK_BYTES):
                f.write(chunk)
                written += len(chunk)
                if written >= next_log:
                    pct = 100 * written / spec.bytes
                    log.info("%s: %.0f%% (%d MB)", spec.path, pct, written >> 20)
                    next_log += PROGRESS_EVERY_BYTES


def download(spec: FileSpec) -> dict:
    """Download one file with resume + retries; returns its record for _downloads.json."""
    target = spec.local_path
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                stream_once(client, spec, part)
                break
            except (httpx.TransportError, RetryableDownloadError) as exc:
                wait = BACKOFF_BASE_S**attempt
                log.warning("%s: %s; resume in %.0fs (try %d)", spec.path, exc, wait, attempt)
                time.sleep(wait)
        else:
            raise RuntimeError(f"{spec.path}: gave up after {MAX_RETRIES} attempts")

    size = part.stat().st_size
    if size != spec.bytes:
        raise RuntimeError(f"{spec.path}: got {size:,} bytes, manifest says {spec.bytes:,}")
    part.replace(target)
    return {
        "url": spec.url,
        "bytes": size,
        "sha256": sha256_of(target),
        "downloaded_at": datetime.now(UTC).isoformat(),
    }


def load_record() -> dict:
    if DOWNLOADS_RECORD.exists():
        return json.loads(DOWNLOADS_RECORD.read_text(encoding="utf-8"))
    return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", metavar="NAME", help="file keys from the manifest")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    files = load_manifest().files
    names = args.only or list(files)
    unknown = set(names) - set(files)
    if unknown:
        parser.error(f"unknown file key(s): {sorted(unknown)}")

    record = load_record()
    for name in names:
        spec = files[name]
        if spec.local_path.exists() and spec.local_path.stat().st_size == spec.bytes:
            log.info("skip %s (complete, %s bytes)", spec.path, f"{spec.bytes:,}")
            continue
        log.info("downloading %s (%.1f MB)", spec.path, spec.bytes / 1e6)
        record[name] = download(spec)
        DOWNLOADS_RECORD.write_text(json.dumps(record, indent=2), encoding="utf-8")
        log.info("saved %s sha256=%s", spec.path, record[name]["sha256"][:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
