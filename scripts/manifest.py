"""Paths and the typed data/manifest.yaml (what the corpus contains)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.yaml"
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
DOWNLOADS_RECORD = RAW_DIR / "_downloads.json"


class DatasetInfo(BaseModel):
    name: str
    homepage: str
    category: str
    licence_note: str


class FileSpec(BaseModel):
    url: str
    path: str  # file name under data/raw/
    bytes: int = Field(gt=0)

    @property
    def local_path(self) -> Path:
        return RAW_DIR / self.path


class Manifest(BaseModel):
    dataset: DatasetInfo
    files: dict[str, FileSpec]


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    """Read and validate data/manifest.yaml. Raises on any missing or malformed field."""
    with path.open(encoding="utf-8") as f:
        return Manifest.model_validate(yaml.safe_load(f))
