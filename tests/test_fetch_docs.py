"""Unit tests for the pure logic in scripts/fetch_docs.py (no network)."""

from pathlib import Path

import pytest
from fetch_docs import (
    SOURCES_CSV,
    DocFormat,
    Source,
    is_official_url,
    load_sources,
    looks_like,
)
from pydantic import ValidationError

ROW = {
    "doc_id": "x",
    "title": "X",
    "source_url": "https://www.goindigo.in/x.html",
    "format": "html",
    "authority": "airline",
    "effective_date": "",
    "downloaded_on": "2026-10-09",
}


@pytest.mark.parametrize(
    "url",
    [
        "https://www.dgca.gov.in/digigov-portal/Upload?attachId=abc",
        "https://dgca.gov.in/a.pdf",
        "https://www.airindia.com/content/dam/air-india/pdfs/air-india-coc.pdf",
        "https://www.akasaair.com/quick-links/baggage",
    ],
)
def test_official_urls_accepted(url: str) -> None:
    assert is_official_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.dgca.gov.in/a.pdf",  # not https
        "https://img.static-kl.com/Passenger-rights-IN.pdf",  # third-party copy
        "https://notdgca.gov.in/a.pdf",  # suffix trick without a dot
        "https://goindigo.in.evil.com/x",  # official name as a subdomain of another site
    ],
)
def test_unofficial_urls_rejected(url: str) -> None:
    assert not is_official_url(url)


def test_source_rejects_unofficial_url() -> None:
    with pytest.raises(ValidationError):
        Source(**{**ROW, "source_url": "https://example.com/x.html"})


def test_source_rejects_unknown_authority() -> None:
    with pytest.raises(ValidationError):
        Source(**{**ROW, "authority": "blog"})


def test_blank_dates_become_none_and_filename_uses_format() -> None:
    source = Source(**ROW)
    assert source.effective_date is None
    assert source.filename == "x.html"


def test_looks_like() -> None:
    assert looks_like(b"%PDF-1.5 ...", DocFormat.PDF)
    assert not looks_like(b"<html>Access denied</html>", DocFormat.PDF)
    assert looks_like(b"<!DOCTYPE html><html lang='en'>", DocFormat.HTML)
    assert not looks_like(b'{"error": "blocked"}', DocFormat.HTML)


def test_load_sources_rejects_duplicate_ids(tmp_path: Path) -> None:
    csv_path = tmp_path / "sources.csv"
    header = ",".join(ROW)
    line = ",".join(ROW.values())
    csv_path.write_text(f"{header}\n{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate doc_id"):
        load_sources(csv_path)


def test_real_manifest_is_valid() -> None:
    """The committed docs/sources.csv must pass every rule (official, typed, unique)."""
    sources = load_sources(SOURCES_CSV)
    assert any(s.authority == "regulator" for s in sources)
