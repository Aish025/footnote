"""Unit tests for corpus_stats.py formatting (the numbers themselves come from SQL)."""

from datetime import UTC, datetime

from corpus_stats import Stats, render_markdown, render_text


def stats(**overrides) -> Stats:
    base = dict(
        products_by_bucket={"cables": 2, "chargers": 1},
        with_features=3,
        with_description=1,
        with_details=3,
        with_price=2,
        reviews=10,
        reviews_by_bucket={"cables": 6, "chargers": 4},
        first_review=datetime(2015, 1, 2, tzinfo=UTC),
        last_review=datetime(2023, 9, 1, tzinfo=UTC),
        verified_ratio=0.9,
        short_reviews=1,
        avg_review_chars=180.0,
        per_product_min=2,
        per_product_p25=2.5,
        per_product_median=3.0,
        per_product_p90=5.0,
        per_product_max=6,
        ratings={1: 1, 5: 9},
        repeat_reviewer_pairs=0,
        qa_pairs=0,
        qa_products=0,
        est_listing_chunks=7,
        est_review_chunks=12,
        est_qa_chunks=0,
        chunks_in_db=0,
    )
    return Stats(**{**base, **overrides})


def test_totals_are_sums_of_sql_parts() -> None:
    s = stats()
    assert s.products == 3
    assert s.est_chunks == 19


def test_text_report_shows_key_figures() -> None:
    text = render_text(stats())
    assert "products .................. 3" in text
    assert "2015-01-02 .. 2023-09-01" in text
    assert "verified purchase ......... 90.0%" in text
    assert "= 19" in text


def test_empty_database_renders_without_errors() -> None:
    empty = stats(
        products_by_bucket={},
        reviews=0,
        reviews_by_bucket={},
        first_review=None,
        last_review=None,
        verified_ratio=None,
        ratings={},
    )
    assert "verified purchase ......... -" in render_text(empty)


def test_markdown_table_has_header_and_rows() -> None:
    md = render_markdown(stats()).splitlines()
    assert md[0] == "| Measure | Value |"
    assert any(line.startswith("| Estimated chunks | 19") for line in md)
