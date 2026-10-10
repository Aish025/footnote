"""Print counts for everything in data/raw/ (issues, fix PRs, comments, release notes, docs).

Usage:
    python scripts/corpus_stats.py              # human-readable report
    python scripts/corpus_stats.py --markdown   # tables to paste into README.md
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from corpus import DOCS_DIR, ISSUES_DIR, RELEASE_NOTES_DIR, Issue

TOP_N = 10
# Prow/CI accounts are "User" type on GitHub, so also match the naming convention.
BOT_LOGIN = re.compile(r"(\[bot\]|-bot|-robot)$")
RELEASE_HEADING = re.compile(r"^# v(\d+\.\d+\.\d+\S*)", re.MULTILINE)
# Change entries are top-level bullets. Skip TOC links ("- [v1.37.1](#v1371)") and dependency
# bumps ("- github.com/foo/bar: v1.2 -> v1.3"; module paths start with a domain), which are not
# behaviour changes. "- `kubelet`/DRA: Fixed ..." is a real entry and must still count.
CHANGELOG_ENTRY = re.compile(r"^- (?!\[)(?![a-z0-9.-]+\.[a-z]+/\S*: )", re.MULTILINE)


class IssueStats(BaseModel):
    issues: int = 0
    closed_from: str | None = None
    closed_to: str | None = None
    with_fix_pr: int = 0
    unique_fix_prs: int = 0
    multi_fix_issues: int = 0
    fix_prs_with_milestone: int = 0
    comments: int = 0
    comments_median: float = 0.0
    comments_max: int = 0
    incomplete_comment_issues: int = 0
    bot_comments: int = 0
    comments_by_association: dict[str, int] = {}
    top_labels: list[tuple[str, int]] = []
    kind_labels: list[tuple[str, int]] = []
    top_sigs: list[tuple[str, int]] = []
    fix_milestones: list[tuple[str, int]] = []
    text_mb: float = 0.0
    skipped_no_merged_fix: int | None = None
    windows_done: int | None = None


class ReleaseNotesStats(BaseModel):
    version: str
    releases: int
    entries: int
    kb: int


class DocsStats(BaseModel):
    version: str
    pages: int
    mb: float
    sha: str | None


class CorpusStats(BaseModel):
    issues: IssueStats
    release_notes: list[ReleaseNotesStats]
    docs: list[DocsStats]


def is_bot(login: str | None, author_type: str | None) -> bool:
    return author_type == "Bot" or bool(login and BOT_LOGIN.search(login))


def issue_stats(issues_dir: Path) -> IssueStats:
    """Aggregate every data/raw/issues/<n>.json (validated as Issue)."""
    files = sorted(p for p in issues_dir.glob("*.json") if p.stem.isdigit())
    stats = IssueStats(issues=len(files))
    labels, assoc, milestones = Counter(), Counter(), Counter()
    per_issue, closed, fix_numbers, text_chars = [], [], set(), 0
    for path in files:
        issue = Issue.model_validate_json(path.read_text(encoding="utf-8"))
        labels.update(issue.labels)
        per_issue.append(len(issue.comments))
        if issue.closed_at:
            closed.append(issue.closed_at.date().isoformat())
        if len(issue.comments) < issue.comments_total:
            stats.incomplete_comment_issues += 1
        stats.with_fix_pr += bool(issue.fix_prs)
        stats.multi_fix_issues += len(issue.fix_prs) > 1
        for pr in issue.fix_prs:
            if pr.number not in fix_numbers:
                fix_numbers.add(pr.number)
                stats.fix_prs_with_milestone += pr.milestone is not None
                milestones[pr.milestone or "(none)"] += 1
        for c in issue.comments:
            assoc[c.author_association] += 1
            stats.bot_comments += is_bot(c.author, c.author_type)
        text_chars += len(issue.title) + len(issue.body)
        text_chars += sum(len(c.body) for c in issue.comments)
        text_chars += sum(len(pr.body) for pr in issue.fix_prs)

    stats.unique_fix_prs = len(fix_numbers)
    stats.comments = sum(per_issue)
    stats.comments_median = statistics.median(per_issue) if per_issue else 0.0
    stats.comments_max = max(per_issue, default=0)
    stats.comments_by_association = dict(assoc.most_common())
    stats.top_labels = labels.most_common(TOP_N)
    stats.kind_labels = [(k, v) for k, v in labels.most_common() if k.startswith("kind/")]
    stats.top_sigs = [(k, v) for k, v in labels.most_common() if k.startswith("sig/")][:TOP_N]
    stats.fix_milestones = milestones.most_common(TOP_N)
    stats.closed_from, stats.closed_to = (min(closed), max(closed)) if closed else (None, None)
    stats.text_mb = round(text_chars / 1e6, 1)

    state_file = issues_dir / "_state.json"
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        stats.skipped_no_merged_fix = state.get("skipped_no_merged_fix")
        stats.windows_done = len(state.get("done_windows", []))
    return stats


def release_notes_stats(notes_dir: Path) -> list[ReleaseNotesStats]:
    """Per CHANGELOG file: how many releases (# v1.x.y headings) and change entries."""
    out = []
    for path in sorted(notes_dir.glob("CHANGELOG-*.md")):
        text = path.read_text(encoding="utf-8")
        out.append(
            ReleaseNotesStats(
                version=path.stem.removeprefix("CHANGELOG-"),
                releases=len(RELEASE_HEADING.findall(text)),
                entries=len(CHANGELOG_ENTRY.findall(text)),
                kb=path.stat().st_size // 1024,
            )
        )
    return out


def docs_stats(docs_dir: Path) -> list[DocsStats]:
    """Per docs version directory: markdown pages, size and pinned commit."""
    out = []
    for vdir in sorted(p for p in docs_dir.glob("v*") if p.is_dir()):
        pages = list(vdir.rglob("*.md"))
        meta = vdir / "_meta.json"
        sha = json.loads(meta.read_text(encoding="utf-8"))["sha"] if meta.exists() else None
        out.append(
            DocsStats(
                version=vdir.name.removeprefix("v"),
                pages=len(pages),
                mb=round(sum(p.stat().st_size for p in pages) / 1e6, 1),
                sha=sha[:12] if sha else None,
            )
        )
    return out


def collect() -> CorpusStats:
    return CorpusStats(
        issues=issue_stats(ISSUES_DIR),
        release_notes=release_notes_stats(RELEASE_NOTES_DIR),
        docs=docs_stats(DOCS_DIR),
    )


def _pairs(items: list[tuple[str, int]]) -> str:
    return ", ".join(f"{k} {v}" for k, v in items) or "-"


def render_text(s: CorpusStats) -> str:
    i = s.issues
    lines = [
        "ISSUES (kubernetes/kubernetes, closed, fixed by a merged PR)",
        f"  issues on disk ............ {i.issues:,}",
        f"  closed between ............ {i.closed_from} .. {i.closed_to}",
        f"  with merged fix PR ........ {i.with_fix_pr:,}  (>1 fix PR: {i.multi_fix_issues:,})",
        f"  unique fix PRs ............ {i.unique_fix_prs:,}"
        f"  (with release milestone: {i.fix_prs_with_milestone:,})",
        f"  comments .................. {i.comments:,}  (median {i.comments_median:g}/issue, "
        f"max {i.comments_max:,}, incomplete issues {i.incomplete_comment_issues})",
        f"  bot comments .............. {i.bot_comments:,}",
        f"  comments by association ... {_pairs(list(i.comments_by_association.items()))}",
        f"  skipped (no merged fix) ... {i.skipped_no_merged_fix}   windows done: {i.windows_done}",
        f"  text (title/body/comments/PR bodies) ... {i.text_mb} MB",
        f"  kind labels ............... {_pairs(i.kind_labels)}",
        f"  top SIGs .................. {_pairs(i.top_sigs)}",
        f"  fix PR milestones ......... {_pairs(i.fix_milestones)}",
        "",
        "RELEASE NOTES (CHANGELOG per minor; releases include alpha/beta/rc)",
        *[
            f"  {r.version:<6} releases {r.releases:>3}   entries {r.entries:>5,}   {r.kb:>5,} KB"
            for r in s.release_notes
        ],
        f"  total entries: {sum(r.entries for r in s.release_notes):,}",
        "",
        "DOCS (kubernetes/website content/en/docs)",
        *[f"  v{d.version:<5} pages {d.pages:>5,}   {d.mb:>5} MB   @{d.sha}" for d in s.docs],
        f"  total pages: {sum(d.pages for d in s.docs):,}",
    ]
    return "\n".join(lines)


def render_markdown(s: CorpusStats) -> str:
    i = s.issues
    rows = [
        "| Source | Count | Notes |",
        "|---|---:|---|",
        f"| Closed issues with merged fix PR | {i.with_fix_pr:,} | closed "
        f"{i.closed_from} to {i.closed_to} |",
        f"| Unique fix PRs | {i.unique_fix_prs:,} | {i.fix_prs_with_milestone:,} with a "
        "release milestone |",
        f"| Issue comments | {i.comments:,} | median {i.comments_median:g}/issue; "
        f"{i.bot_comments:,} from bots |",
        f"| Release-note entries | {sum(r.entries for r in s.release_notes):,} | "
        f"CHANGELOG {', '.join(r.version for r in s.release_notes)} |",
        f"| Docs pages | {sum(d.pages for d in s.docs):,} | "
        f"{len(s.docs)} versions ({', '.join(d.version for d in s.docs)}) |",
    ]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--markdown", action="store_true", help="print a README table")
    args = parser.parse_args(argv)
    stats = collect()
    print(render_markdown(stats) if args.markdown else render_text(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
