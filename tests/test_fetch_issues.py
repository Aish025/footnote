"""Unit tests for the pure logic of scripts/fetch_issues.py and scripts/github_client.py."""

from datetime import UTC, date, datetime

import pytest

from fetch_issues import (
    COMMENTS_QUERY,
    SEARCH_QUERY,
    FetchState,
    date_windows,
    merged_fix_prs,
    parse_issue,
    search_query,
)
from github_client import GitHubClient, MissingTokenError, rate_limit_wait
from settings import Settings


def pr_node(number: int, merged: bool) -> dict:
    return {
        "number": number,
        "url": f"https://github.com/kubernetes/kubernetes/pull/{number}",
        "title": f"fix {number}",
        "body": None,
        "state": "MERGED" if merged else "CLOSED",
        "merged": merged,
        "mergedAt": "2026-09-02T10:00:00Z" if merged else None,
        "baseRefName": "master",
        "mergeCommit": {"oid": "abc123"} if merged else None,
        "milestone": {"title": "v1.37"},
        "labels": {"nodes": [{"name": "kind/bug"}]},
        "author": {"login": "dev"},
    }


def issue_node(closing: list[dict], closer: dict | None) -> dict:
    return {
        "number": 1,
        "url": "https://github.com/kubernetes/kubernetes/issues/1",
        "title": "kubelet crash",
        "body": "panic: runtime error",
        "state": "CLOSED",
        "stateReason": "COMPLETED",
        "createdAt": "2026-08-01T00:00:00Z",
        "closedAt": "2026-09-02T10:00:00Z",
        "authorAssociation": "NONE",
        "author": None,  # deleted account
        "milestone": None,
        "labels": {"nodes": [{"name": "kind/bug"}, {"name": "sig/node"}]},
        "comments": {"totalCount": 1, "pageInfo": {"hasNextPage": False}, "nodes": []},
        "closedByPullRequestsReferences": {"nodes": closing},
        "timelineItems": {"nodes": [{"closer": closer}] if closer else []},
    }


COMMENT = {
    "fullDatabaseId": "3141592653",
    "url": "https://github.com/kubernetes/kubernetes/issues/1#issuecomment-3141592653",
    "body": "/close",
    "createdAt": "2026-09-02T10:00:00Z",
    "authorAssociation": "MEMBER",
    "author": {"login": "k8s-ci-robot", "__typename": "User"},
}


def test_date_windows_cover_range_newest_first_without_gaps() -> None:
    windows = date_windows(date(2026, 10, 1), date(2026, 9, 1), 14)
    assert windows[0] == (date(2026, 9, 17), date(2026, 10, 1))
    assert windows[-1][0] == date(2026, 9, 1)  # last window clipped to the floor
    for (start, _), (_, next_end) in zip(windows, windows[1:], strict=False):
        assert next_end == start  # contiguous, no overlap


def test_search_query_uses_inclusive_last_day() -> None:
    q = search_query("kubernetes/kubernetes", date(2026, 9, 17), date(2026, 10, 1))
    assert q.endswith("closed:2026-09-17..2026-09-30")
    assert "is:issue" in q and "linked:pr" in q


def test_merged_fix_prs_dedupes_and_drops_unmerged() -> None:
    node = issue_node(closing=[pr_node(10, True), pr_node(11, False)], closer=None)
    closer = {"__typename": "PullRequest", **pr_node(10, True)}
    node["timelineItems"]["nodes"] = [{"closer": closer}]
    assert [p.number for p in merged_fix_prs(node)] == [10]


def test_merged_fix_prs_ignores_commit_closer() -> None:
    node = issue_node(closing=[], closer={"__typename": "Commit"})
    assert merged_fix_prs(node) == []


def test_parse_issue_handles_ghost_author_and_null_body() -> None:
    node = issue_node(closing=[pr_node(10, True)], closer=None)
    issue = parse_issue(node, [COMMENT], datetime(2026, 10, 10, tzinfo=UTC))
    assert issue.author is None
    assert issue.labels == ["kind/bug", "sig/node"]
    assert issue.fix_prs[0].body == ""
    assert issue.fix_prs[0].milestone == "v1.37"
    assert issue.comments[0].id == "3141592653"  # kept as string: exceeds 32-bit int


def test_graphql_documents_only_define_fragments_they_use() -> None:
    # GitHub rejects a query that defines an unused fragment.
    assert "fragment pr" not in COMMENTS_QUERY
    assert "...pr" in SEARCH_QUERY and "...comment" in SEARCH_QUERY


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (200, {}, None),
        (403, {"retry-after": "30"}, 30.0),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1100"}, 101.0),
        (429, {}, 60.0),
        (403, {"x-ratelimit-remaining": "4000"}, 60.0),
    ],
)
def test_rate_limit_wait(status: int, headers: dict, expected: float | None) -> None:
    assert rate_limit_wait(status, headers, now=1000.0, fallback_s=60.0) == expected


def test_client_requires_token() -> None:
    with pytest.raises(MissingTokenError):
        GitHubClient(Settings(_env_file=None, github_token=None))


def test_state_round_trips() -> None:
    state = FetchState(done_windows=["2026-09-17..2026-10-01"], cursor="Y3Vyc29y", pages=3)
    assert FetchState.model_validate_json(state.model_dump_json()) == state
