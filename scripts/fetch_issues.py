"""Fetch recent closed kubernetes/kubernetes issues that were fixed by a merged PR.

For each issue we save title, body, labels, milestone, ALL comments and the merged fix PR(s)
to data/raw/issues/<number>.json. Scope comes from data/manifest.yaml (issues section).

How it works:
1. Split [closed_after, closed_before) into windows of `window_days`, newest first.
   GitHub search returns at most 1,000 hits per query; small windows stay under that.
2. Search each window with `is:issue is:closed linked:pr` via GraphQL, `page_size` issues
   per request, including comments, labels and the PRs that closed the issue.
3. Keep only issues with at least one MERGED closing PR (the fix).
4. After every page, save a checkpoint (data/raw/issues/_state.json). Re-running resumes
   from the last cursor; issue files are idempotent (same number -> same file).

Usage:
    python scripts/fetch_issues.py              # until manifest target_count is reached
    python scripts/fetch_issues.py --target 20  # small smoke run
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from corpus import (
    ISSUES_STATE_FILE,
    Comment,
    Issue,
    IssuesSpec,
    PullRequest,
    issue_path,
    load_manifest,
    saved_issue_numbers,
    write_json_atomic,
)
from github_client import GitHubClient, GitHubServerError, MissingTokenError
from settings import get_settings

log = logging.getLogger("fetch_issues")

GITHUB_SEARCH_MAX_RESULTS = 1000  # hard cap of GitHub search, per query

PR_FRAGMENT = """
fragment pr on PullRequest {
  number url title body state merged mergedAt baseRefName
  mergeCommit { oid }
  milestone { title }
  labels(first: 30) { nodes { name } }
  author { login }
}
"""

COMMENT_FRAGMENT = """
fragment comment on IssueComment {
  fullDatabaseId url body createdAt authorAssociation
  author { login __typename }
}
"""

SEARCH_QUERY = (
    """
query($q: String!, $first: Int!, $after: String, $comments: Int!) {
  search(query: $q, type: ISSUE, first: $first, after: $after) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        number url title body state stateReason createdAt closedAt authorAssociation
        author { login }
        milestone { title }
        labels(first: 30) { nodes { name } }
        comments(first: $comments) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes { ...comment }
        }
        closedByPullRequestsReferences(first: 5) { nodes { ...pr } }
        timelineItems(itemTypes: [CLOSED_EVENT], last: 1) {
          nodes { ... on ClosedEvent { closer { __typename ...pr } } }
        }
      }
    }
  }
}
"""
    + PR_FRAGMENT
    + COMMENT_FRAGMENT
)

COMMENTS_QUERY = (
    """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      comments(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { ...comment }
      }
    }
  }
}
"""
    + COMMENT_FRAGMENT
)


# --- Pure logic (unit-tested) -----------------------------------------------------------


def date_windows(before: date, after: date, days: int) -> list[tuple[date, date]]:
    """Half-open windows [start, end) covering [after, before), newest first."""
    windows = []
    end = before
    while end > after:
        start = max(after, end - timedelta(days=days))
        windows.append((start, end))
        end = start
    return windows


def window_label(start: date, end: date) -> str:
    return f"{start.isoformat()}..{end.isoformat()}"


def search_query(repo: str, start: date, end: date) -> str:
    """GitHub search string for issues closed in [start, end). Search ranges are inclusive."""
    last_day = end - timedelta(days=1)
    return f"repo:{repo} is:issue is:closed linked:pr closed:{start.isoformat()}..{last_day}"


def _login(node: dict[str, Any] | None) -> str | None:
    return node.get("login") if node else None


def _names(conn: dict[str, Any] | None) -> list[str]:
    return [n["name"] for n in (conn or {}).get("nodes", [])]


def _title(node: dict[str, Any] | None) -> str | None:
    return node.get("title") if node else None


def parse_comment(node: dict[str, Any]) -> Comment:
    author = node.get("author")
    return Comment(
        id=str(node["fullDatabaseId"]),
        url=node["url"],
        author=_login(author),
        author_type=author.get("__typename") if author else None,
        author_association=node["authorAssociation"],
        created_at=node["createdAt"],
        body=node["body"] or "",
    )


def parse_pr(node: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=node["number"],
        url=node["url"],
        title=node["title"],
        body=node["body"] or "",
        state=node["state"],
        merged=node["merged"],
        merged_at=node.get("mergedAt"),
        merge_commit_sha=(node.get("mergeCommit") or {}).get("oid"),
        base_ref=node["baseRefName"],
        milestone=_title(node.get("milestone")),
        labels=_names(node.get("labels")),
        author=_login(node.get("author")),
    )


def merged_fix_prs(issue_node: dict[str, Any]) -> list[PullRequest]:
    """Merged PRs that closed the issue, from both the closing reference and the close event.

    The two sources overlap; deduplicate by PR number. Unmerged or non-PR closers
    (a commit, a person) are ignored.
    """
    candidates = list((issue_node.get("closedByPullRequestsReferences") or {}).get("nodes", []))
    for event in (issue_node.get("timelineItems") or {}).get("nodes", []):
        closer = (event or {}).get("closer") or {}
        if closer.get("__typename") == "PullRequest":
            candidates.append(closer)
    by_number = {n["number"]: parse_pr(n) for n in candidates if n and n.get("merged")}
    return [by_number[k] for k in sorted(by_number)]


def parse_issue(
    node: dict[str, Any], comment_nodes: list[dict[str, Any]], fetched_at: datetime
) -> Issue:
    return Issue(
        number=node["number"],
        url=node["url"],
        title=node["title"],
        body=node["body"] or "",
        state=node["state"],
        state_reason=node.get("stateReason"),
        created_at=node["createdAt"],
        closed_at=node.get("closedAt"),
        author=_login(node.get("author")),
        author_association=node["authorAssociation"],
        labels=_names(node.get("labels")),
        milestone=_title(node.get("milestone")),
        comments_total=node["comments"]["totalCount"],
        comments=[parse_comment(c) for c in comment_nodes],
        fix_prs=merged_fix_prs(node),
        fetched_at=fetched_at,
    )


# --- Checkpoint -------------------------------------------------------------------------


class FetchState(BaseModel):
    """Resume point: finished windows, plus the cursor inside the current window."""

    done_windows: list[str] = []
    window: str | None = None
    cursor: str | None = None
    skipped_no_merged_fix: int = 0
    pages: int = 0


def load_state() -> FetchState:
    if ISSUES_STATE_FILE.exists():
        return FetchState.model_validate_json(ISSUES_STATE_FILE.read_text(encoding="utf-8"))
    return FetchState()


def save_state(state: FetchState) -> None:
    write_json_atomic(ISSUES_STATE_FILE, state.model_dump())


# --- Network ----------------------------------------------------------------------------


def all_comment_nodes(client: GitHubClient, repo: str, node: dict[str, Any]) -> list[dict]:
    """Inline comments plus any further pages (busy issues have hundreds of comments)."""
    comments = node["comments"]
    nodes = list(comments["nodes"])
    page, owner, name = comments["pageInfo"], *repo.split("/")
    while page["hasNextPage"]:
        data = client.graphql(
            COMMENTS_QUERY,
            {"owner": owner, "name": name, "number": node["number"], "after": page["endCursor"]},
        )
        conn = data["repository"]["issue"]["comments"]
        nodes.extend(conn["nodes"])
        page = conn["pageInfo"]
    return nodes


def search_page(
    client: GitHubClient, spec: IssuesSpec, query: str, cursor: str | None, page_size: int
) -> tuple[dict[str, Any], int]:
    """One search page; halves the page size when GitHub times out on a heavy query."""
    settings = client.settings
    while True:
        try:
            variables = {
                "q": query,
                "first": page_size,
                "after": cursor,
                "comments": spec.inline_comments,
            }
            return client.graphql(SEARCH_QUERY, variables)["search"], page_size
        except GitHubServerError:
            if page_size <= settings.min_page_size:
                raise
            page_size = max(settings.min_page_size, page_size // 2)
            log.warning("GitHub timed out; retrying with page_size=%d", page_size)


def fetch_window(
    client: GitHubClient,
    spec: IssuesSpec,
    start: date,
    end: date,
    state: FetchState,
    saved: set[int],
    target: int,
) -> bool:
    """Fetch one date window. Returns True if the target was reached (stop everything)."""
    label = window_label(start, end)
    cursor = state.cursor if state.window == label else None
    state.window = label
    query = search_query(spec.repo, start, end)
    page_size = spec.page_size
    while True:
        result, page_size = search_page(client, spec, query, cursor, page_size)
        if cursor is None and result["issueCount"] > GITHUB_SEARCH_MAX_RESULTS:
            hits = result["issueCount"]
            log.warning("%s has %d hits > 1,000 cap: lower window_days", label, hits)
        fetched_at = datetime.now(UTC)
        for node in result["nodes"]:
            if not node:  # non-Issue search hits come back as empty objects
                continue
            issue = parse_issue(node, all_comment_nodes(client, spec.repo, node), fetched_at)
            if spec.require_merged_fix and not issue.fix_prs:
                state.skipped_no_merged_fix += 1
                continue
            write_json_atomic(issue_path(issue.number), issue.model_dump(mode="json"))
            saved.add(issue.number)
            if len(saved) >= target:
                save_state(state)
                return True
        state.pages += 1
        cursor = result["pageInfo"]["endCursor"]
        state.cursor = cursor
        save_state(state)
        log.info(
            "%s page %d: saved %d/%d, skipped (no merged fix) %d, budget left %s",
            label,
            state.pages,
            len(saved),
            target,
            state.skipped_no_merged_fix,
            client.remaining,
        )
        if not result["pageInfo"]["hasNextPage"]:
            state.done_windows.append(label)
            state.window, state.cursor = None, None
            save_state(state)
            return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=int, help="override manifest target_count")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    spec = load_manifest().issues
    target = args.target or spec.target_count
    try:
        client = GitHubClient(get_settings())
    except MissingTokenError as exc:
        log.error("%s", exc)
        return 2

    state = load_state()
    saved = saved_issue_numbers()
    done = len(state.done_windows)
    log.info("resuming with %d issues on disk, %d windows done", len(saved), done)
    for start, end in date_windows(spec.closed_before, spec.closed_after, spec.window_days):
        if len(saved) >= target:
            break
        if window_label(start, end) in state.done_windows:
            continue
        if fetch_window(client, spec, start, end, state, saved, target):
            break

    log.info("done: %d issues with a merged fix on disk (target %d)", len(saved), target)
    if len(saved) < target:
        log.warning("window exhausted before target: lower closed_after in the manifest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
