"""Paths and the typed data/manifest.yaml (what the corpus contains)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.yaml"
RAW_DIR = REPO_ROOT / "data" / "raw"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"
DOWNLOADS_RECORD = RAW_DIR / "_downloads.json"
SUBSET_PRODUCTS = PROCESSED_DIR / "products.jsonl"
SUBSET_REVIEWS = PROCESSED_DIR / "reviews.jsonl"
SUBSET_QA = PROCESSED_DIR / "qa.jsonl"
SUBSET_REPORT = PROCESSED_DIR / "subset_report.json"
QA_OVERLAP_REPORT = PROCESSED_DIR / "qa_overlap.json"


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


class SubsetRule(BaseModel):
    buckets: dict[str, list[str]]  # bucket -> category names (first matching bucket wins)
    require_listing_text: bool
    min_reviews: int = Field(gt=0)
    max_reviews: int = Field(gt=0)
    per_bucket: int = Field(gt=0)
    order_salt: str

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> SubsetRule:
        if self.min_reviews > self.max_reviews:
            raise ValueError("min_reviews must be <= max_reviews")
        return self


class QARule(BaseModel):
    sources: list[str]  # keys into `files`
    min_matched_products: int = Field(gt=0)


class Manifest(BaseModel):
    dataset: DatasetInfo
    files: dict[str, FileSpec]
    subset: SubsetRule
    qa: QARule


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    """Read and validate data/manifest.yaml. Raises on any missing or malformed field."""
    with path.open(encoding="utf-8") as f:
        return Manifest.model_validate(yaml.safe_load(f))
