"""Unit tests for row parsing and COPY escaping in scripts/load_db.py (no database)."""

from datetime import UTC, datetime
from decimal import Decimal

from db import split_statements
from load_db import copy_field, copy_line, dedupe, parse_price, product_row, review_row

RAW_REVIEW = {
    "rating": 2.0,
    "title": "Stopped charging",
    "text": "Worked for a week.\nThen\tnothing.",
    "images": [],
    "asin": "B0VARIANT",
    "parent_asin": "B0PARENT",
    "user_id": "AGUSER",
    "timestamp": 1588687728923,
    "helpful_vote": 3,
    "verified_purchase": True,
}


def test_parse_price() -> None:
    assert parse_price("29.99") == Decimal("29.99")
    assert parse_price(12) == Decimal("12")
    assert parse_price("$1,299.00") == Decimal("1299.00")
    assert parse_price(None) is None
    assert parse_price("—") is None
    assert parse_price("from 9.99") is None


def test_review_row_id_is_stable_and_timestamp_is_utc() -> None:
    a, b = review_row(RAW_REVIEW), review_row(dict(RAW_REVIEW))
    assert a.review_id == b.review_id and len(a.review_id) == 24
    assert a.created_at == datetime(2020, 5, 5, 14, 8, 48, 923000, tzinfo=UTC)
    assert a.rating == 2 and a.product_id == "B0PARENT" and a.has_images is False


def test_review_id_differs_per_user() -> None:
    other = review_row({**RAW_REVIEW, "user_id": "AGOTHER"})
    assert other.review_id != review_row(RAW_REVIEW).review_id


def test_product_row_defaults_missing_lists() -> None:
    meta = {"parent_asin": "B0P", "subset_bucket": "cables", "title": "USB-C", "price": None}
    row = product_row(meta)
    assert row.features == [] and row.description == [] and row.details == {}


def test_copy_field_escaping() -> None:
    assert copy_field(None) == r"\N"
    assert copy_field(True) == "t"
    assert copy_field("a\tb\nc\\d\r") == "a\\tb\\nc\\\\d\\r"
    assert copy_field("nul\x00byte") == "nulbyte"
    assert copy_field(["x", "y"]) == '["x", "y"]'


def test_copy_line_has_one_field_per_value_and_ends_with_newline() -> None:
    line = copy_line(["a\tb", None, 3])
    assert line.endswith("\n") and line.count("\t") == 2


def test_dedupe_counts_exact_duplicates() -> None:
    rows = [review_row(RAW_REVIEW), review_row(RAW_REVIEW), review_row({**RAW_REVIEW, "asin": "X"})]
    stats = {"duplicates": 0}
    assert len(list(dedupe(rows, "review_id", stats))) == 2
    assert stats["duplicates"] == 1


def test_split_statements_ignores_comments() -> None:
    sql = "-- header\nCREATE TABLE a (x INT);\n-- note; with semicolon\nCREATE INDEX i ON a (x);\n"
    assert split_statements(sql) == ["CREATE TABLE a (x INT)", "CREATE INDEX i ON a (x)"]
