"""Print corpus statistics computed by SQL over the loaded Postgres tables.

Hard rule 5: numbers come only from the database, so every figure here is a SQL query
(fixed text, parameters bound by the driver). Python only formats the results.

Usage:
    python scripts/corpus_stats.py              # human-readable report
    python scripts/corpus_stats.py --markdown   # table for README.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from pydantic import BaseModel

from config import get_settings
from db import connect

# --- SQL (one query per figure group) ---------------------------------------------------

SQL_PRODUCTS = """
SELECT subset_bucket, COUNT(*) FROM products GROUP BY subset_bucket ORDER BY subset_bucket"""

SQL_LISTING = """
SELECT COUNT(*) FILTER (WHERE cardinality(features) > 0),
       COUNT(*) FILTER (WHERE cardinality(description) > 0),
       COUNT(*) FILTER (WHERE details <> '{}'::jsonb),
       COUNT(*) FILTER (WHERE price IS NOT NULL)
FROM products"""

SQL_REVIEWS = """
SELECT COUNT(*), MIN(created_at), MAX(created_at),
       AVG(verified_purchase::int)::float,
       COUNT(*) FILTER (WHERE length(btrim(text)) < 20),
       AVG(length(text))::float
FROM reviews"""

SQL_REVIEWS_BY_BUCKET = """
SELECT p.subset_bucket, COUNT(*) FROM reviews r JOIN products p USING (product_id)
GROUP BY p.subset_bucket ORDER BY p.subset_bucket"""

SQL_PER_PRODUCT = """
WITH c AS (SELECT p.product_id, COUNT(r.review_id) AS n
           FROM products p LEFT JOIN reviews r USING (product_id) GROUP BY p.product_id)
SELECT MIN(n), percentile_cont(0.25) WITHIN GROUP (ORDER BY n),
       percentile_cont(0.5) WITHIN GROUP (ORDER BY n),
       percentile_cont(0.9) WITHIN GROUP (ORDER BY n), MAX(n)
FROM c"""

SQL_RATINGS = "SELECT rating, COUNT(*) FROM reviews GROUP BY rating ORDER BY rating"

# Same user reviewing the same product more than once: a dedup concern for aggregates.
SQL_REPEAT_REVIEWERS = """
SELECT COUNT(*) FROM (SELECT 1 FROM reviews GROUP BY product_id, user_id HAVING COUNT(*) > 1) t"""

SQL_QA = "SELECT COUNT(*), COUNT(DISTINCT product_id) FROM qa"

# Estimated retrieval units before Phase 2 chunking exists:
#   listing: one chunk per non-empty field group (title+features, description, details)
#   review:  ceil(len(title + text) / :chunk_chars), at least 1
#   qa:      one chunk per Q&A pair
SQL_EST_CHUNKS = """
SELECT
  (SELECT COALESCE(SUM(1 + (cardinality(description) > 0)::int + (details <> '{}'::jsonb)::int),
                   0) FROM products),
  (SELECT COALESCE(SUM(GREATEST(1, CEIL(length(title || ' ' || text)::numeric / :chunk_chars))),
                   0)::bigint FROM reviews),
  (SELECT COUNT(*) FROM qa),
  (SELECT COUNT(*) FROM chunks)"""


class Stats(BaseModel):
    products_by_bucket: dict[str, int]
    with_features: int
    with_description: int
    with_details: int
    with_price: int
    reviews: int
    reviews_by_bucket: dict[str, int]
    first_review: datetime | None
    last_review: datetime | None
    verified_ratio: float | None
    short_reviews: int
    avg_review_chars: float | None
    per_product_min: int
    per_product_p25: float
    per_product_median: float
    per_product_p90: float
    per_product_max: int
    ratings: dict[int, int]
    repeat_reviewer_pairs: int
    qa_pairs: int
    qa_products: int
    est_listing_chunks: int
    est_review_chunks: int
    est_qa_chunks: int
    chunks_in_db: int

    @property
    def products(self) -> int:
        return sum(self.products_by_bucket.values())

    @property
    def est_chunks(self) -> int:
        return self.est_listing_chunks + self.est_review_chunks + self.est_qa_chunks


def collect(conn, chunk_chars: int) -> Stats:
    """Run every stats query and return the typed result."""
    feats, desc, details, price = conn.run(SQL_LISTING)[0]
    n, first, last, verified, short, avg_len = conn.run(SQL_REVIEWS)[0]
    pmin, p25, p50, p90, pmax = conn.run(SQL_PER_PRODUCT)[0]
    qa_pairs, qa_products = conn.run(SQL_QA)[0]
    est_listing, est_review, est_qa, in_db = conn.run(SQL_EST_CHUNKS, chunk_chars=chunk_chars)[0]
    return Stats(
        products_by_bucket=dict(conn.run(SQL_PRODUCTS)),
        with_features=feats,
        with_description=desc,
        with_details=details,
        with_price=price,
        reviews=n,
        reviews_by_bucket=dict(conn.run(SQL_REVIEWS_BY_BUCKET)),
        first_review=first,
        last_review=last,
        verified_ratio=verified,
        short_reviews=short,
        avg_review_chars=avg_len,
        per_product_min=pmin or 0,
        per_product_p25=p25 or 0,
        per_product_median=p50 or 0,
        per_product_p90=p90 or 0,
        per_product_max=pmax or 0,
        ratings=dict(conn.run(SQL_RATINGS)),
        repeat_reviewer_pairs=conn.run(SQL_REPEAT_REVIEWERS)[0][0],
        qa_pairs=qa_pairs,
        qa_products=qa_products,
        est_listing_chunks=est_listing,
        est_review_chunks=est_review,
        est_qa_chunks=est_qa,
        chunks_in_db=in_db,
    )


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "-"


def _ratio(r: float | None) -> str:
    return f"{100 * r:.1f}%" if r is not None else "-"


def _date(d: datetime | None) -> str:
    return d.date().isoformat() if d else "-"


def render_text(s: Stats) -> str:
    buckets = ", ".join(
        f"{b} {s.products_by_bucket[b]:,} ({s.reviews_by_bucket.get(b, 0):,} reviews)"
        for b in s.products_by_bucket
    )
    ratings = "  ".join(f"{k}*: {_pct(v, s.reviews)}" for k, v in sorted(s.ratings.items()))
    return "\n".join(
        [
            "PRODUCTS (listing claims)",
            f"  products .................. {s.products:,}",
            f"  by bucket ................. {buckets}",
            f"  with features / description / details / price ... "
            f"{_pct(s.with_features, s.products)} / {_pct(s.with_description, s.products)} / "
            f"{_pct(s.with_details, s.products)} / {_pct(s.with_price, s.products)}",
            "",
            "REVIEWS",
            f"  reviews ................... {s.reviews:,}",
            f"  date range ................ {_date(s.first_review)} .. {_date(s.last_review)}",
            f"  verified purchase ......... {_ratio(s.verified_ratio)}",
            f"  reviews per product ....... min {s.per_product_min:,}  "
            f"p25 {s.per_product_p25:g}  median {s.per_product_median:g}  "
            f"p90 {s.per_product_p90:g}  max {s.per_product_max:,}",
            f"  ratings ................... {ratings}",
            f"  avg length ................ {s.avg_review_chars or 0:.0f} chars; "
            f"under 20 chars: {s.short_reviews:,} ({_pct(s.short_reviews, s.reviews)})",
            f"  same user, same product, >1 review ... {s.repeat_reviewer_pairs:,} pairs",
            "",
            "Q&A (older McAuley datasets, matched by ASIN)",
            f"  pairs ..................... {s.qa_pairs:,} for {s.qa_products:,} products",
            "",
            "ESTIMATED CHUNKS (before Phase 2 chunking)",
            f"  listing {s.est_listing_chunks:,} + review {s.est_review_chunks:,} + "
            f"qa {s.est_qa_chunks:,} = {s.est_chunks:,}   (chunks table now: {s.chunks_in_db:,})",
        ]
    )


def render_markdown(s: Stats) -> str:
    buckets = ", ".join(f"{b} {n:,}" for b, n in s.products_by_bucket.items())
    rows = [
        "| Measure | Value |",
        "|---|---:|",
        f"| Products | {s.products:,} ({buckets}) |",
        f"| Reviews | {s.reviews:,} |",
        f"| Review date range | {_date(s.first_review)} to {_date(s.last_review)} |",
        f"| Verified purchase | {_ratio(s.verified_ratio)} |",
        f"| Reviews per product (min / median / p90 / max) | {s.per_product_min:,} / "
        f"{s.per_product_median:g} / {s.per_product_p90:g} / {s.per_product_max:,} |",
        f"| Q&A pairs | {s.qa_pairs:,} ({s.qa_products:,} products) |",
        f"| Estimated chunks | {s.est_chunks:,} (listing {s.est_listing_chunks:,}, "
        f"review {s.est_review_chunks:,}, qa {s.est_qa_chunks:,}) |",
    ]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--markdown", action="store_true", help="print a README table")
    args = parser.parse_args(argv)
    settings = get_settings()
    conn = connect(settings)
    try:
        stats = collect(conn, settings.review_chunk_chars)
    finally:
        conn.close()
    print(render_markdown(stats) if args.markdown else render_text(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
