# Phase 0 — Setup & corpus (incident version)

Gate: about 5K recent closed Kubernetes issues with linked fixes, plus matching docs and release notes, fetched reproducibly, with stats in the README.

## 1. What was built

| File | What it is |
|---|---|
| `docker-compose.yml`, `db/init/01_extensions.sql` | Local Postgres 17 + pgvector 0.8. The SQL file enables the `vector` extension on first start. There are no tables yet (Phase 2). |
| `.env.example` | Template for `GITHUB_TOKEN` and the Postgres settings. The real `.env` is gitignored. |
| `data/manifest.yaml` | **What** the corpus contains: issue window and target, release-note versions, docs versions → branches. |
| `scripts/corpus.py` | Paths, the typed `Manifest`, the raw record models (`Issue`, `Comment`, `PullRequest`), atomic JSON writes. |
| `scripts/settings.py` | pydantic-settings: token and network tuning (retries, backoff, rate-limit thresholds). |
| `scripts/github_client.py` | GraphQL client: handles primary, secondary and 5xx limits. |
| `scripts/fetch_issues.py` | Closed issues + all comments + merged fix PRs → `data/raw/issues/<n>.json`. Resumable. |
| `scripts/fetch_docs.py` | CHANGELOG 1.33–1.37 + kubernetes/website docs per version → `data/raw/release_notes/`, `data/raw/docs/v1.xx/`. |
| `scripts/corpus_stats.py` | Counts and noise indicators. `--markdown` prints the README table. |
| `tests/` | 25 unit tests on pure logic (windows, fix-PR extraction, rate-limit waits, stats). |
| `.gitignore`, `.gitattributes` | Keep `data/raw/`, `data/processed/` and `.env` out of git. Force LF line endings. |

## 2. How data flows

**Issues**, run with `python scripts/fetch_issues.py`:
1. `load_manifest()` reads and validates `data/manifest.yaml`. `get_settings()` reads `GITHUB_TOKEN` from `.env`. If the token is missing, `GitHubClient` raises `MissingTokenError` and the script exits with code 2.
2. `load_state()` reads the checkpoint `data/raw/issues/_state.json`, and `saved_issue_numbers()` lists the issues already on disk.
3. `date_windows(closed_before, closed_after, 14)` makes 14-day windows, newest first. Windows listed in `done_windows` are skipped.
4. `fetch_window()` builds `search_query()`, for example `repo:kubernetes/kubernetes is:issue is:closed linked:pr closed:2026-09-17..2026-09-30`, and calls `search_page()`, which runs `client.graphql(SEARCH_QUERY)`. One request returns 20 issues, each with:
   - labels and milestone
   - its first 50 comments
   - `closedByPullRequestsReferences`
   - the close event's `closer`
5. For each issue:
   - `all_comment_nodes()` pages in any remaining comments.
   - `merged_fix_prs()` merges both PR sources, dedupes them by number and keeps only `merged == true`.
   - `parse_issue()` builds an `Issue` model.
   - Issues with no merged fix are counted as skipped. The rest are written with `write_json_atomic()` (temp file + rename).
6. After each page, the cursor is saved to `_state.json`. A crash or Ctrl-C loses at most one page, and re-running continues from that cursor.
7. `GitHubClient.graphql()` handles limits around every call:
   - Proactive pause when `x-ratelimit-remaining` drops below 100 points.
   - On 403/429: wait for `Retry-After`, or until the reset time, or 60 s.
   - On 5xx: exponential backoff. If it still fails, `search_page()` halves the page size (heavy queries are what time out).

**Release notes and docs**, run with `python scripts/fetch_docs.py`:
1. `resolve_ref()` calls `git ls-remote` to get the current SHA of a branch. This uses no API budget.
2. `fetch_release_notes()` downloads `CHANGELOG/CHANGELOG-1.xx.md` at that single SHA and checks `looks_like_changelog()`.
3. `fetch_docs_version()` does a shallow, blob-less, sparse clone of kubernetes/website at `release-1.xx` (or `main` for 1.37), copies `content/en/docs`, and deletes the clone.
4. Each folder gets a `_meta.json` (repo, ref, sha). A re-run skips any folder whose SHA hasn't changed.

**Stats**, run with `python scripts/corpus_stats.py`. `collect()` calls `issue_stats()`, `release_notes_stats()` and `docs_stats()`, which build a `CorpusStats` model. `render_text()` or `render_markdown()` prints it.

## 3. Why

- **[D-001](../../DECISIONS.md) Postgres + pgvector.** The data is relational (issue → fix PR → release → docs), and filtering by version, labels and state is core. One store means one source of truth. Compared with: NumPy, Chroma, Qdrant. It would be revisited if p95 latency or recall breaks at 1M–10M chunks (v3).
- **D-002 Corpus and secrets out of git; LF line endings.** Required by hard rules 7 and 10. Raw logs can contain secrets, and the manifest plus scripts are the reproducible record.
- **D-003 One YAML manifest with typed models.** Scope is reviewable in one file. The pinned `closed_before` means re-runs select the same issues. Schema drift fails at fetch time, not in Phase 2.
- **D-004 GraphQL search in date windows; merged closing PR = the fix.** Compared with REST, which needs about 4–5 calls per issue: GraphQL gets about 20 issues per call. Windows avoid GitHub's 1,000-results-per-search cap. Two link sources are deduped.
- **D-005 SHA-pinned raw changelogs and a sparse git clone for docs.** Needs no token. Compared with the contents API (about 8K calls) or the full website tarball (hundreds of MB).
- **D-006 Stats are computed by pure, tested functions.** A naive bullet count was 46% too high (4,860 vs 3,327).

## 4. Trade-offs and limits

- **Missed fix links.** Only issues GitHub links to a merged PR are kept. An issue fixed by a PR that just says "fixes kubernetes/kubernetes#123" across repos, or by a cherry-pick, can be missed. The kept issues are "fixed issues GitHub knows about", which is a biased sample. Fix: parse "Fixes #N" from PR bodies; measure the miss rate on the golden set.
- **The corpus is below the ~5K target (measured).** Back to 2022-01-01 there are only 2,799 closed issues with a merged, GitHub-linked fix, roughly 600 a year. Another 800 had a linked PR that was never merged. Getting to 5K means going back to about 2019 (older versions, staler fixes) or loosening the "merged fix" rule. That is a scope decision for the owner (D-008).
- **Version coverage gap (measured).** Fix PRs ship in v1.24–v1.38, about 150–220 per minor. Docs and changelogs cover only 1.33–1.37, so just 924 of 2,799 issues (33%) can complete issue → fix → release → docs. Fix: add the 1.24–1.32 and 1.38 changelogs (cheap) and older doc branches.
- **Upstream drift.** Comments can be edited or deleted after fetching. Issue snapshots are not pinned to a SHA; there is only `fetched_at`.
- **Bot detection is heuristic.** `k8s-ci-robot` is a normal "User" account on GitHub, so the stats also match login names ending in `-bot`/`-robot`. Real filtering is Phase 2.
- **Secrets are stored raw in data/raw.** Redaction happens before indexing (Phase 2, hard rule 8). data/raw is local and gitignored, but it is not clean.
- **No incremental sync.** Re-runs resume, but they don't pick up issues closed after `closed_before`. Incremental sync with `updated:>since` comes in v2.

## 5. Numbers

- Release notes: 5 minors (1.33–1.37), 3,327 change entries, 1.9 MB.
- Docs: 7,989 markdown pages across 5 versions (1,495 → 1,721 per version), 66 MB of markdown. Fetched in about 95 s; a re-run skips everything.
- Postgres 17.11 + pgvector 0.8.7 came up healthy via `docker compose up -d`.
- Unit tests: 25 passed. ruff: clean.
- Issues (fetched 2026-10-10):
  - **Fetch:** 2,799 issues with a merged fix PR, closed 2022-01-03 → 2026-09-30. It took 237 pages and 124 windows in about 11 minutes, using about 500 of the 5,000 hourly GraphQL points. 800 issues were skipped (linked PR never merged).
  - **Fix PRs:** 2,945 unique, 2,774 with a release milestone, 172 issues with more than one fix PR.
  - **Comments:** 27,463 in total (median 6 per issue, max 223), no incomplete threads; 3,817 (14%) from bots.
  - **Labels:** kind/bug 1,231, kind/feature 412, kind/failing-test 378, kind/flake 319. The top SIGs are node (751) and api-machinery (509).
  - **Version coverage:** only 924 issues (33%) have a fix shipped in 1.33–1.37.
- Retrieval and answer quality: pending (Phase 2).

## 6. Interview questions

1. **Why GraphQL rather than REST for issues?**
   REST needs separate calls for the issue, each page of comments, and the timeline (to find the closing PR): about 4–5 calls per issue, or 20–25K calls for 5K issues. That is 4–5 hours at 5,000 requests per hour. One GraphQL search page returns 20 issues with their comments, labels and closing PRs.

2. **GitHub search caps results at 1,000. How do you get 5,000?**
   `date_windows()` splits the range into 14-day windows and each window is searched separately. If a window reports more than 1,000 hits, the script logs a warning to lower `window_days`.

3. **What does "rate-limit aware" mean concretely in your code?**
   Three cases:
   - Primary budget: pause below 100 remaining points until `x-ratelimit-reset`.
   - Secondary limits (403/429): honour `Retry-After`, or wait for the reset, or wait 60 s.
   - 5xx/timeouts: exponential backoff, then halve the page size.
   `rate_limit_wait()` is a pure function with 5 tests.

4. **How is the fetch resumable, and why is re-running safe?**
   The cursor and finished windows are saved after every page. Issue files are keyed by issue number and written atomically, so re-fetching a page overwrites the same files with the same content. Writes are idempotent; nothing is duplicated.

5. **How do you decide which PR "fixed" an issue?**
   Two sources: `closedByPullRequestsReferences` (closing keywords) and the close event's `closer`. They are deduped by PR number and only merged PRs are kept. A commit closer or an unmerged PR doesn't count. Limitation: fixes that GitHub doesn't link are missed.

6. **Why is the comment id a string?**
   GitHub comment ids have passed 2³¹, which is why GraphQL has `fullDatabaseId` (a BigInt). Storing it as a string avoids overflow in any 32-bit consumer. It is the stable id for citations (hard rule 4).

7. **Why not commit the data, and how is it reproducible then?**
   Hard rule 7: it's large, it changes upstream, and raw logs may contain secrets. Reproducibility comes from three things:
   - The manifest pins the issue window.
   - Docs and changelogs are pinned to commit SHAs recorded in `_meta.json`.
   - Issue fetches are deterministic given the upstream state.

8. **Why a sparse clone for docs instead of the GitHub API?**
   There are about 8K files across 5 versions. The contents API would cost about 8K calls; a sparse, shallow clone downloads only `content/en/docs` (about 18 MB per version) in about 18 s, using no API budget.

9. **Why only Kubernetes 1.33–1.37?**
   These are the 5 versions kubernetes.io keeps online. Version-aware answers need docs for the user's version. If fix-PR milestones show many older versions, extend the manifest.

10. **Why Postgres + pgvector and not a vector DB?**
    The data is relational (issue → PR → release → docs), and filtering by version, labels and state is central. SQL joins plus vector search in one transactional store keeps the system simple. It would be revisited if latency or recall breaks at 1M+ chunks (D-001).

11. **You aimed for 5K issues and got 2,799. Why, and what would you do?**
    The fetch walked every 14-day window back to the 2022 floor. Kubernetes only closes about 600 issues a year with a merged, GitHub-linked fix PR. The options:
    - Go back to about 2019. This means older versions and fixes that no longer apply.
    - Accept issues without a merged fix. This weakens the "how was it fixed" ground truth.
    - Add more repos (planned for v2).
    I reported the shortfall instead of quietly loosening the filter, because the merged fix is what makes fix-lookup answers verifiable.

12. **What did corpus_stats catch?**
    Counting `- ` bullets overcounted release-note entries by 46%: TOC links and go.mod dependency bumps aren't changes. A wrong denominator now would have distorted every later per-source metric.

## 7. Try it yourself

1. **Run.** Run `python scripts/fetch_issues.py --target 20` and open one `data/raw/issues/<n>.json`. Find the fix PR's milestone, then find that version's CHANGELOG entry for the PR number in `data/raw/release_notes/`. You have just done the multi-hop the project must automate.
2. **Break.** Start a fetch with `--target 200` and press Ctrl-C midway. Inspect `_state.json`, then re-run. Confirm it resumes from the cursor and that the issue count only grows.
3. **Explain.** In `github_client.py`, explain why a 5xx raises `GitHubServerError` after 3 tries instead of retrying forever, and why the *caller* (`search_page`) is the one that shrinks the page size.
