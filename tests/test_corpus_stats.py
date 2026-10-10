"""Unit tests for scripts/corpus_stats.py on a tiny fake corpus (no network)."""

from pathlib import Path

from corpus_stats import docs_stats, is_bot, issue_stats, release_notes_stats

ISSUE = """{
 "number": 7, "url": "u", "title": "t", "body": "b", "state": "CLOSED", "state_reason": null,
 "created_at": "2026-08-01T00:00:00Z", "closed_at": "2026-09-02T00:00:00Z",
 "author": "a", "author_association": "NONE", "labels": ["kind/bug", "sig/node"],
 "milestone": null, "comments_total": 3, "fetched_at": "2026-10-10T00:00:00Z",
 "comments": [
  {"id": "1", "url": "u", "author": "k8s-ci-robot", "author_type": "User",
   "author_association": "CONTRIBUTOR", "created_at": "2026-09-01T00:00:00Z", "body": "/close"},
  {"id": "2", "url": "u", "author": "maint", "author_type": "User",
   "author_association": "MEMBER", "created_at": "2026-09-01T00:00:00Z", "body": "fixed"}
 ],
 "fix_prs": [{"number": 99, "url": "u", "title": "t", "body": "", "state": "MERGED",
   "merged": true, "merged_at": null, "merge_commit_sha": null, "base_ref": "master",
   "milestone": "v1.37", "labels": [], "author": "dev"}]
}"""


def test_is_bot() -> None:
    assert is_bot("k8s-ci-robot", "User")
    assert is_bot("dependabot[bot]", "Bot")
    assert not is_bot("robotics-fan", "User")
    assert not is_bot(None, None)


def test_issue_stats(tmp_path: Path) -> None:
    (tmp_path / "7.json").write_text(ISSUE, encoding="utf-8")
    (tmp_path / "_state.json").write_text('{"skipped_no_merged_fix": 4}', encoding="utf-8")
    s = issue_stats(tmp_path)
    assert (s.issues, s.with_fix_pr, s.unique_fix_prs, s.fix_prs_with_milestone) == (1, 1, 1, 1)
    assert (s.comments, s.bot_comments, s.incomplete_comment_issues) == (2, 1, 1)
    assert s.comments_by_association == {"CONTRIBUTOR": 1, "MEMBER": 1}
    assert s.closed_from == "2026-09-02"
    assert s.skipped_no_merged_fix == 4


def test_empty_corpus_does_not_crash(tmp_path: Path) -> None:
    assert issue_stats(tmp_path).issues == 0
    assert release_notes_stats(tmp_path) == []
    assert docs_stats(tmp_path) == []


def test_release_notes_counts_releases_and_entries(tmp_path: Path) -> None:
    text = "# v1.37.1\n\n- Fixed a bug\n- Another\n\n# v1.37.0\n\n- Feature\n"
    (tmp_path / "CHANGELOG-1.37.md").write_text(text, encoding="utf-8")
    [r] = release_notes_stats(tmp_path)
    assert (r.version, r.releases, r.entries) == ("1.37", 2, 3)


def test_release_notes_skip_toc_and_dependency_bumps(tmp_path: Path) -> None:
    text = (
        "- [v1.37.1](#v1371)\n"  # TOC link
        "# v1.37.1\n"
        "- `kubelet`/DRA: Fixed duplicate CDI device IDs\n"  # real entry with a slash
        "- github.com/google/cadvisor: v0.52.1 -> v0.53.0\n"  # dependency bump
        "- sigs.k8s.io/yaml: v1.4.0 -> v1.5.0\n"  # dependency bump
    )
    (tmp_path / "CHANGELOG-1.37.md").write_text(text, encoding="utf-8")
    [r] = release_notes_stats(tmp_path)
    assert r.entries == 1


def test_docs_stats_counts_markdown_only(tmp_path: Path) -> None:
    v = tmp_path / "v1.37" / "concepts"
    v.mkdir(parents=True)
    (v / "pods.md").write_text("# Pods", encoding="utf-8")
    (v / "diagram.svg").write_text("<svg/>", encoding="utf-8")
    [d] = docs_stats(tmp_path)
    assert (d.version, d.pages, d.sha) == ("1.37", 1, None)
