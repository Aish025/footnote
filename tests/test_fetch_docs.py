"""Unit tests for the pure checks in scripts/fetch_docs.py."""

from fetch_docs import github_url, looks_like_changelog


def test_changelog_check_accepts_real_heading() -> None:
    assert looks_like_changelog("<!-- toc -->\n# v1.37.1\n\n## Downloads", "1.37")


def test_changelog_check_rejects_error_page_and_wrong_version() -> None:
    assert not looks_like_changelog("404: Not Found", "1.37")
    assert not looks_like_changelog("# v1.36.4\n", "1.37")


def test_github_url() -> None:
    assert github_url("kubernetes/website") == "https://github.com/kubernetes/website.git"
