"""Unit tests for the pure resume logic in scripts/download_data.py and the manifest."""

import pytest

from download_data import RetryableDownloadError, range_header, resume_offset
from manifest import load_manifest


def test_range_header_only_when_resuming() -> None:
    assert range_header(0) == {}
    assert range_header(1048576) == {"Range": "bytes=1048576-"}


def test_resume_offset_honours_partial_content() -> None:
    assert resume_offset(206, 500) == 500


def test_resume_offset_restarts_when_server_ignores_range() -> None:
    # A 200 means the server sent the whole file: we must overwrite, not append.
    assert resume_offset(200, 500) == 0


def test_resume_offset_raises_retryable_on_other_status() -> None:
    with pytest.raises(RetryableDownloadError):
        resume_offset(503, 0)


def test_manifest_lists_official_files_with_sizes() -> None:
    manifest = load_manifest()
    assert manifest.dataset.category == "Cell_Phones_and_Accessories"
    assert {"meta", "reviews"} <= set(manifest.files)
    for spec in manifest.files.values():
        assert spec.url.startswith("https://mcauleylab.ucsd.edu/")
        assert spec.bytes > 0
