"""Load the v1 subset (data/processed/*.jsonl) into Postgres: products, reviews, qa.

Steps per table:
1. Parse each JSONL line into a typed row (ProductRow / ReviewRow / QARow).
2. Drop exact duplicates by primary key (counted and reported).
3. COPY the rows into a temporary staging table in batches (text format, escaped here).
4. Upsert staging -> real table with ON CONFLICT DO UPDATE, in one transaction.
Re-running is safe: the same input produces the same rows (idempotent).
The chunks table is created but stays empty until Phase 2.

Usage:
    python scripts/load_db.py
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import sys
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from config import Settings, get_settings
from db import apply_schema, connect
from manifest import SUBSET_PRODUCTS, SUBSET_QA, SUBSET_REVIEWS

log = logging.getLogger("load_db")


# --- Typed rows -------------------------------------------------------------------------


class ProductRow(BaseModel):
    product_id: str
    subset_bucket: str
    title: str
    main_category: str | None
    categories: list[str]
    features: list[str]
    description: list[str]
    details: dict[str, Any]
    price: Decimal | None
    store: str | None
    average_rating: float | None
    rating_number: int | None


class ReviewRow(BaseModel):
    review_id: str
    product_id: str
    asin: str
    user_id: str
    rating: int
    title: str
    text: str
    created_at: datetime
    verified_purchase: bool
    helpful_vote: int
    has_images: bool


class QARow(BaseModel):
    qa_id: str
    product_id: str
    source: str
    question: str
    answer: str
    question_type: str | None
    answered_on: str | None


def parse_price(value: Any) -> Decimal | None:
    """Listing price as a number, or None for missing/non-numeric values like '—'."""
    if value is None:
        return None
    try:
        price = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        return None
    return price if price.is_finite() and price >= 0 else None


def stable_id(*parts: str) -> str:
    """Short deterministic id from the fields that identify a record."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def product_row(meta: dict) -> ProductRow:
    return ProductRow(
        product_id=meta["parent_asin"],
        subset_bucket=meta["subset_bucket"],
        title=meta["title"],
        main_category=meta.get("main_category"),
        categories=meta.get("categories") or [],
        features=meta.get("features") or [],
        description=meta.get("description") or [],
        details=meta.get("details") or {},
        price=parse_price(meta.get("price")),
        store=meta.get("store"),
        average_rating=meta.get("average_rating"),
        rating_number=meta.get("rating_number"),
    )


def review_row(raw: dict) -> ReviewRow:
    return ReviewRow(
        review_id=stable_id(raw["user_id"], raw["parent_asin"], raw["asin"], str(raw["timestamp"])),
        product_id=raw["parent_asin"],
        asin=raw["asin"],
        user_id=raw["user_id"],
        rating=int(raw["rating"]),
        title=raw.get("title") or "",
        text=raw.get("text") or "",
        created_at=datetime.fromtimestamp(raw["timestamp"] / 1000, tz=UTC),
        verified_purchase=bool(raw.get("verified_purchase")),
        helpful_vote=int(raw.get("helpful_vote") or 0),
        has_images=bool(raw.get("images")),
    )


# --- COPY text format -------------------------------------------------------------------


def copy_field(value: Any) -> str:
    r"""One field in Postgres COPY text format: \N for NULL, escaped tabs/newlines/backslashes.

    NUL characters are removed: Postgres text columns cannot store them.
    """
    if value is None:
        return r"\N"
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, datetime):
        value = value.isoformat()
    text = str(value).replace("\x00", "")
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def copy_line(values: Iterable[Any]) -> str:
    return "\t".join(copy_field(v) for v in values) + "\n"


# --- Loading ----------------------------------------------------------------------------


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def batched(rows: Iterable[BaseModel], size: int) -> Iterator[list[BaseModel]]:
    batch: list[BaseModel] = []
    for row in rows:
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def dedupe(rows: Iterable[BaseModel], key: str, stats: dict) -> Iterator[BaseModel]:
    """Yield rows with an unseen primary key; count the exact duplicates dropped."""
    seen: set[str] = set()
    for row in rows:
        pk = getattr(row, key)
        if pk in seen:
            stats["duplicates"] += 1
            continue
        seen.add(pk)
        yield row


# Staging keeps arrays/objects as JSONB; the upsert converts them to the real column types.
STAGING_DDL = {
    "products": """CREATE TEMP TABLE stage (product_id TEXT, subset_bucket TEXT, title TEXT,
        main_category TEXT, categories JSONB, features JSONB, description JSONB, details JSONB,
        price NUMERIC, store TEXT, average_rating NUMERIC, rating_number INTEGER)""",
    "reviews": "CREATE TEMP TABLE stage (LIKE reviews)",
    "qa": "CREATE TEMP TABLE stage (LIKE qa)",
}

UPSERT_SQL = {
    "products": """INSERT INTO products
        SELECT product_id, subset_bucket, title, main_category,
               ARRAY(SELECT jsonb_array_elements_text(categories)),
               ARRAY(SELECT jsonb_array_elements_text(features)),
               ARRAY(SELECT jsonb_array_elements_text(description)),
               details, price, store, average_rating, rating_number
        FROM stage
        ON CONFLICT (product_id) DO UPDATE SET
            subset_bucket = EXCLUDED.subset_bucket, title = EXCLUDED.title,
            main_category = EXCLUDED.main_category, categories = EXCLUDED.categories,
            features = EXCLUDED.features, description = EXCLUDED.description,
            details = EXCLUDED.details, price = EXCLUDED.price, store = EXCLUDED.store,
            average_rating = EXCLUDED.average_rating, rating_number = EXCLUDED.rating_number""",
    "reviews": """INSERT INTO reviews SELECT * FROM stage
        ON CONFLICT (review_id) DO UPDATE SET
            product_id = EXCLUDED.product_id, asin = EXCLUDED.asin, user_id = EXCLUDED.user_id,
            rating = EXCLUDED.rating, title = EXCLUDED.title, text = EXCLUDED.text,
            created_at = EXCLUDED.created_at, verified_purchase = EXCLUDED.verified_purchase,
            helpful_vote = EXCLUDED.helpful_vote, has_images = EXCLUDED.has_images""",
    "qa": """INSERT INTO qa SELECT * FROM stage
        ON CONFLICT (qa_id) DO UPDATE SET
            product_id = EXCLUDED.product_id, source = EXCLUDED.source,
            question = EXCLUDED.question, answer = EXCLUDED.answer,
            question_type = EXCLUDED.question_type, answered_on = EXCLUDED.answered_on""",
}

PRIMARY_KEY = {"products": "product_id", "reviews": "review_id", "qa": "qa_id"}


def load_table(conn, table: str, rows: Iterable[BaseModel], settings: Settings) -> dict:
    """Stage + upsert one table inside a single transaction. Returns load stats."""
    stats = {"rows": 0, "duplicates": 0}
    conn.run("BEGIN")
    try:
        conn.run(STAGING_DDL[table])
        for batch in batched(dedupe(rows, PRIMARY_KEY[table], stats), settings.load_batch_rows):
            buffer = io.StringIO("".join(copy_line(r.model_dump().values()) for r in batch))
            conn.run("COPY stage FROM STDIN", stream=buffer)
            stats["rows"] += len(batch)
            log.info("%s: staged %s rows", table, f"{stats['rows']:,}")
        conn.run(UPSERT_SQL[table])
        conn.run("DROP TABLE stage")
        conn.run("COMMIT")
    except Exception:
        conn.run("ROLLBACK")
        raise
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for path in (SUBSET_PRODUCTS, SUBSET_REVIEWS):
        if not path.exists():
            log.error("missing %s: run scripts/select_subset.py first", path.name)
            return 2
    settings = get_settings()
    conn = connect(settings)
    try:
        apply_schema(conn, settings)
        results = {
            "products": load_table(
                conn, "products", map(product_row, read_jsonl(SUBSET_PRODUCTS)), settings
            ),
            "reviews": load_table(
                conn, "reviews", map(review_row, read_jsonl(SUBSET_REVIEWS)), settings
            ),
        }
        if SUBSET_QA.exists():
            qa_rows = (QARow(**r) for r in read_jsonl(SUBSET_QA))
            results["qa"] = load_table(conn, "qa", qa_rows, settings)
        else:
            log.info("no %s: qa table left empty (see check_qa_overlap.py)", SUBSET_QA.name)
        conn.run("ANALYZE")
    finally:
        conn.close()
    for table, stats in results.items():
        log.info(
            "%s: %s rows loaded, %s exact duplicates dropped",
            table,
            f"{stats['rows']:,}",
            f"{stats['duplicates']:,}",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
