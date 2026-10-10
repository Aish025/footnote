"""Unit tests for the manifest and record helpers in scripts/corpus.py."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from corpus import MANIFEST_PATH, Manifest, load_manifest


def test_committed_manifest_is_valid() -> None:
    manifest = load_manifest()
    assert manifest.issues.repo == "kubernetes/kubernetes"
    assert set(manifest.release_notes.versions) == set(manifest.docs.versions)


def test_manifest_rejects_inverted_window(tmp_path: Path) -> None:
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    data["issues"]["closed_after"] = data["issues"]["closed_before"]
    with pytest.raises(ValidationError, match="closed_after"):
        Manifest.model_validate(data)


def test_manifest_rejects_page_size_over_github_max() -> None:
    data = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    data["issues"]["page_size"] = 500
    with pytest.raises(ValidationError):
        Manifest.model_validate(data)
