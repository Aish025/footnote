"""Unit tests for the v1 subset rule in scripts/select_subset.py."""

from collections import Counter

from manifest import SubsetRule, load_manifest
from select_subset import bucket_for, choose, has_listing_text, order_key, parent_asin_of

BUCKETS = {"chargers": ["Chargers & Power Adapters"], "cases": ["Basic Cases", "Flip Cases"]}


def rule(**overrides) -> SubsetRule:
    base = dict(
        buckets=BUCKETS,
        require_listing_text=True,
        min_reviews=2,
        max_reviews=5,
        per_bucket=2,
        order_salt="t",
    )
    return SubsetRule(**{**base, **overrides})


def test_bucket_matches_any_level_and_first_bucket_wins() -> None:
    path = ["Cell Phones & Accessories", "Cases, Holsters & Sleeves", "Flip Cases"]
    assert bucket_for(path, BUCKETS) == "cases"
    both = ["Accessories", "Chargers & Power Adapters", "Basic Cases"]
    assert bucket_for(both, BUCKETS) == "chargers"  # manifest order decides
    assert bucket_for(["Accessories", "Stands"], BUCKETS) is None
    assert bucket_for([], BUCKETS) is None


def test_listing_text_needs_title_and_features_or_description() -> None:
    assert has_listing_text({"title": "USB-C cable", "features": ["3 ft"], "description": []})
    assert has_listing_text({"title": "Case", "features": [], "description": ["Slim fit"]})
    assert not has_listing_text({"title": "Case", "features": [" "], "description": []})
    assert not has_listing_text({"title": "", "features": ["x"]})


def test_choose_applies_band_and_quota_deterministically() -> None:
    candidates = {"A": "cases", "B": "cases", "C": "cases", "D": "cases", "E": "chargers"}
    counts = Counter({"A": 3, "B": 5, "C": 4, "D": 99, "E": 1})  # D too many, E too few
    chosen = choose(candidates, counts, rule())
    assert set(chosen.values()) == {"cases"}
    assert len(chosen) == 2  # quota
    expected = sorted(["A", "B", "C"], key=lambda a: order_key(a, "t"))[:2]
    assert sorted(chosen) == sorted(expected)
    assert choose(candidates, counts, rule()) == chosen  # same input -> same subset


def test_salt_changes_the_order() -> None:
    assert order_key("B00X", "a") != order_key("B00X", "b")


def test_parent_asin_regex() -> None:
    line = '{"rating": 5.0, "asin": "B01", "parent_asin": "B0PARENT", "user_id": "U"}'
    assert parent_asin_of(line) == "B0PARENT"
    assert parent_asin_of('{"asin": "B01"}') is None


def test_manifest_subset_rule_is_valid() -> None:
    subset = load_manifest().subset
    assert subset.min_reviews <= subset.max_reviews
    assert "chargers" in subset.buckets
