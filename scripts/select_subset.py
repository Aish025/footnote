"""Select the v1 product subset (rule in data/manifest.yaml `subset`) and extract its data.

Three streaming passes over the gzipped raw files (nothing is fully decompressed to disk):
1. meta    -> candidate products: in a bucket, and with listing text if required.
2. reviews -> review count per candidate (parent_asin is pulled out with a regex: fast).
3. reviews -> every review of the selected products.
Selection: candidates with min_reviews..max_reviews reviews, up to per_bucket per bucket in
sha256(salt + parent_asin) order.

Outputs (data/processed/, gitignored):
    products.jsonl  reviews.jsonl  subset_report.json (funnel counts per bucket)

Usage:
    python scripts/select_subset.py
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path

from manifest import (
    PROCESSED_DIR,
    SUBSET_PRODUCTS,
    SUBSET_REPORT,
    SUBSET_REVIEWS,
    SubsetRule,
    load_manifest,
)

log = logging.getLogger("select_subset")

PARENT_ASIN = re.compile(r'"parent_asin":\s*"([^"]+)"')
# Large fields that are never evidence (image/video URLs, co-purchase lists).
DROP_META_FIELDS = ("images", "videos", "bought_together")
PROGRESS_EVERY_LINES = 2_000_000


# --- Pure rule logic (unit-tested) ------------------------------------------------------


def bucket_for(categories: list[str], buckets: dict[str, list[str]]) -> str | None:
    """First bucket whose category names appear anywhere in the product's category path."""
    path = set(categories or [])
    for bucket, names in buckets.items():
        if path.intersection(names):
            return bucket
    return None


def has_listing_text(meta: dict) -> bool:
    """True if the listing has a title and at least one feature bullet or description line."""

    def nonblank(items: list | None) -> bool:
        return any(str(x).strip() for x in items or [])

    return bool((meta.get("title") or "").strip()) and (
        nonblank(meta.get("features")) or nonblank(meta.get("description"))
    )


def order_key(parent_asin: str, salt: str) -> str:
    """Deterministic pseudo-random order: reproducible and independent of popularity."""
    return hashlib.sha256(f"{salt}:{parent_asin}".encode()).hexdigest()


def choose(candidates: dict[str, str], review_counts: Counter, rule: SubsetRule) -> dict[str, str]:
    """Apply the review-count band and per-bucket quota. Returns parent_asin -> bucket."""
    by_bucket: dict[str, list[str]] = defaultdict(list)
    for asin, bucket in candidates.items():
        if rule.min_reviews <= review_counts.get(asin, 0) <= rule.max_reviews:
            by_bucket[bucket].append(asin)
    chosen = {}
    for bucket, asins in by_bucket.items():
        asins.sort(key=lambda a: order_key(a, rule.order_salt))
        chosen.update({a: bucket for a in asins[: rule.per_bucket]})
    return chosen


def parent_asin_of(line: str) -> str | None:
    match = PARENT_ASIN.search(line)
    return match.group(1) if match else None


# --- Streaming passes -------------------------------------------------------------------


def read_lines(path: Path, label: str) -> Iterator[str]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if i % PROGRESS_EVERY_LINES == 0:
                log.info("%s: %s lines", label, f"{i:,}")
            yield line


def find_candidates(meta_path: Path, rule: SubsetRule, funnel: dict) -> dict[str, str]:
    """Pass 1: parent_asin -> bucket for products in a bucket (with listing text)."""
    candidates = {}
    for line in read_lines(meta_path, "meta"):
        meta = json.loads(line)
        funnel["meta_products"] += 1
        bucket = bucket_for(meta.get("categories") or [], rule.buckets)
        if bucket is None:
            continue
        funnel[f"{bucket}.in_bucket"] += 1
        if rule.require_listing_text and not has_listing_text(meta):
            continue
        funnel[f"{bucket}.with_listing_text"] += 1
        candidates[meta["parent_asin"]] = bucket
    return candidates


def count_reviews(reviews_path: Path, candidates: dict[str, str], funnel: dict) -> Counter:
    """Pass 2: number of reviews per candidate product."""
    counts: Counter = Counter()
    for line in read_lines(reviews_path, "reviews count"):
        funnel["review_lines"] += 1
        asin = parent_asin_of(line)
        if asin in candidates:
            counts[asin] += 1
    return counts


def write_products(meta_path: Path, chosen: dict[str, str]) -> int:
    """Re-read meta and write the chosen products (plus their bucket) as JSONL."""
    written = 0
    with SUBSET_PRODUCTS.open("w", encoding="utf-8") as out:
        for line in read_lines(meta_path, "meta write"):
            meta = json.loads(line)
            bucket = chosen.get(meta["parent_asin"])
            if bucket is None:
                continue
            for field in DROP_META_FIELDS:
                meta.pop(field, None)
            meta["subset_bucket"] = bucket
            out.write(json.dumps(meta, ensure_ascii=False) + "\n")
            written += 1
    return written


def write_reviews(reviews_path: Path, chosen: dict[str, str]) -> int:
    """Pass 3: every review line of a chosen product, copied as-is."""
    written = 0
    with SUBSET_REVIEWS.open("w", encoding="utf-8") as out:
        for line in read_lines(reviews_path, "reviews write"):
            if parent_asin_of(line) in chosen:
                out.write(line if line.endswith("\n") else line + "\n")
                written += 1
    return written


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    manifest = load_manifest()
    rule = manifest.subset
    meta_path = manifest.files["meta"].local_path
    reviews_path = manifest.files["reviews"].local_path
    for p in (meta_path, reviews_path):
        if not p.exists():
            log.error("missing %s: run scripts/download_data.py first", p.name)
            return 2
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    funnel: dict = Counter()
    candidates = find_candidates(meta_path, rule, funnel)
    counts = count_reviews(reviews_path, candidates, funnel)
    for asin, bucket in candidates.items():
        if rule.min_reviews <= counts.get(asin, 0) <= rule.max_reviews:
            funnel[f"{bucket}.in_review_band"] += 1
    chosen = choose(candidates, counts, rule)
    for bucket in chosen.values():
        funnel[f"{bucket}.selected"] += 1

    products = write_products(meta_path, chosen)
    reviews = write_reviews(reviews_path, chosen)
    report = {
        "rule": rule.model_dump(),
        "funnel": dict(sorted(funnel.items())),
        "selected_products": products,
        "selected_review_lines": reviews,
    }
    SUBSET_REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("selected %d products, %d review lines -> %s", products, reviews, PROCESSED_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
