"""Shared corpus definitions: paths, the typed manifest, and the raw record models.

Every fetch script reads data/manifest.yaml through `load_manifest()` and writes records
defined here, so corpus_stats.py (and Phase 2 ingestion) read one well-defined format.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.yaml"
RAW_DIR = REPO_ROOT / "data" / "raw"
ISSUES_DIR = RAW_DIR / "issues"
RELEASE_NOTES_DIR = RAW_DIR / "release_notes"
DOCS_DIR = RAW_DIR / "docs"
ISSUES_STATE_FILE = ISSUES_DIR / "_state.json"


# --- Manifest ---------------------------------------------------------------------------


class IssuesSpec(BaseModel):
    repo: str
    target_count: int = Field(gt=0)
    closed_before: date
    closed_after: date
    window_days: int = Field(gt=0)
    page_size: int = Field(gt=0, le=100)
    inline_comments: int = Field(gt=0, le=100)
    require_merged_fix: bool = True

    @model_validator(mode="after")
    def window_is_ordered(self) -> IssuesSpec:
        if self.closed_after >= self.closed_before:
            raise ValueError("closed_after must be earlier than closed_before")
        return self


class ReleaseNotesSpec(BaseModel):
    repo: str
    ref: str
    path_template: str
    versions: list[str]


class DocsSpec(BaseModel):
    repo: str
    path: str
    versions: dict[str, str]  # version -> git ref (branch)


class Manifest(BaseModel):
    issues: IssuesSpec
    release_notes: ReleaseNotesSpec
    docs: DocsSpec


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    """Read and validate data/manifest.yaml. Raises on any missing or malformed field."""
    with path.open(encoding="utf-8") as f:
        return Manifest.model_validate(yaml.safe_load(f))


# --- Raw records (one JSON file per issue) ----------------------------------------------


class Comment(BaseModel):
    id: str  # GitHub fullDatabaseId: stable across re-fetches, used for citations
    url: str
    author: str | None  # None when the account was deleted ("ghost")
    author_type: str | None  # "User" or "Bot" (k8s-ci-robot is a "User"; filtered in Phase 2)
    author_association: str  # OWNER/MEMBER/COLLABORATOR/CONTRIBUTOR/NONE -> evidence priority
    created_at: datetime
    body: str


class PullRequest(BaseModel):
    number: int
    url: str
    title: str
    body: str
    state: str
    merged: bool
    merged_at: datetime | None
    merge_commit_sha: str | None
    base_ref: str
    milestone: str | None  # e.g. "v1.34": the release that shipped the fix
    labels: list[str]
    author: str | None


class Issue(BaseModel):
    number: int  # stable id for the issue
    url: str
    title: str
    body: str
    state: str
    state_reason: str | None
    created_at: datetime
    closed_at: datetime | None
    author: str | None
    author_association: str
    labels: list[str]
    milestone: str | None
    comments_total: int
    comments: list[Comment]
    fix_prs: list[PullRequest]  # merged PRs that closed this issue
    fetched_at: datetime


def write_json_atomic(path: Path, data: dict) -> None:
    """Write JSON via a temp file + rename, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def issue_path(number: int) -> Path:
    return ISSUES_DIR / f"{number}.json"


def saved_issue_numbers() -> set[int]:
    """Issue numbers already on disk (used for resume and stats)."""
    if not ISSUES_DIR.exists():
        return set()
    return {int(p.stem) for p in ISSUES_DIR.glob("*.json") if p.stem.isdigit()}
