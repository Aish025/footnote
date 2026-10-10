# Decisions

## D-001: PostgreSQL + pgvector as the single store (text, metadata, vectors)
- Decision: One Postgres 17 + pgvector container (docker-compose.yml, image pgvector/pgvector:pg17); extension enabled by db/init; schema deferred to Phase 2.
- Options considered: In-memory NumPy, Chroma, Qdrant, Postgres + pgvector.
- Why this one: Data is relational (issue → fix PR → release → docs) and metadata filtering (labels, version, state) is core; one store means one source of truth, SQL joins and transactional upserts. HNSW available.
- Would change if: p95 latency or recall@5 at 1M–10M chunks breaks the budget → compare against Qdrant in v3.
- Measured: pending

## D-002: Corpus and secrets out of git; LF line endings everywhere
- Decision: .gitignore covers data/raw/, data/processed/, .env*; .gitattributes forces LF for code/data/SQL/YAML and marks binaries.
- Options considered: Commit a small sample corpus; Git LFS; rely on per-machine core.autocrlf.
- Why this one: Hard rules 7 and 10. The corpus is large, changes upstream and may contain secrets from logs; the manifest + fetch scripts are the reproducible record. Owner is on Windows (autocrlf=true) but Docker/CI are Linux — CRLF breaks shell/SQL init scripts and JSONL parsing.
- Would change if: Upstream issues get edited/deleted so re-fetches drift enough to move eval numbers → snapshot data/raw to object storage with a version id.
- Measured: n/a (setup)

## D-003: data/manifest.yaml is the single definition of the corpus
- Decision: YAML manifest (issues window/target, release-note versions, docs versions→branches) validated by Pydantic in scripts/corpus.py; records (Issue/Comment/PullRequest) are Pydantic models; secrets/tuning via pydantic-settings.
- Options considered: Hard-code scope in scripts; CLI flags only; CSV manifest (old project).
- Why this one: One reviewable file defines "what is in the corpus"; pinned closed_before makes re-runs select the same issues; typed models catch upstream schema drift at fetch time, not at Phase 2.
- Would change if: Multiple repos (v2) make one flat file unwieldy → one manifest section per source.
- Measured: n/a (config)

## D-004: Issues via GitHub GraphQL search in date windows, merged closing PR = the fix
- Decision: GraphQL `search` (`is:issue is:closed linked:pr closed:<window>`), 14-day windows newest-first, 20 issues/request with comments, labels and closing PRs inline; keep issues whose closing PR is merged; checkpoint cursor after every page.
- Options considered: REST (1 call per issue + per comments page + timeline → ~4–5 calls/issue); GH Archive/BigQuery dumps; scraping. Fix-link source: closing references vs. close event vs. text "Fixes #N" parsing.
- Why this one: GraphQL fetches ~20 issues with their PRs in one request (fits 5K points/h); windows dodge the 1,000-result search cap; both closing references and close events are used and deduped. Rate limits: proactive pause at <100 points, Retry-After/reset on 403/429, backoff + halve page size on 5xx.
- Would change if: >5% of golden-set issues lack a found fix PR (link miss) → add "Fixes #N" body parsing; or v2 volume (100K+) → incremental `updated:>since` sync.
- Measured: 13 unit tests on pure logic; live fetch pending GITHUB_TOKEN.

## D-005: Release notes via SHA-pinned raw files; docs via sparse shallow git clone
- Decision: CHANGELOG-<minor>.md for 1.33–1.37 from raw.githubusercontent at one SHA (git ls-remote); kubernetes/website content/en/docs per version branch via `git clone --depth 1 --filter=blob:none --sparse`; _meta.json records repo/ref/SHA; skip when SHA unchanged.
- Options considered: GitHub contents API (1 call/file, ~8K files, burns rate limit); full website tarball (hundreds of MB incl. images/translations); scraping kubernetes.io HTML.
- Why this one: No token or API budget needed; downloads only the docs subtree (~18 MB/version); markdown source is cleaner than rendered HTML; SHAs make the snapshot reproducible. Versions = the 5 kubernetes.io keeps online.
- Would change if: Fetched issues mostly reference versions <1.33 → extend versions (old release branches exist).
- Measured: 5 changelogs (1.9 MB) + 5 doc versions (7,989 md pages, 89 MB) in ~95 s; re-run skips all.

## D-006: corpus_stats.py reports what Phase 2 will actually index
- Decision: Typed CorpusStats from pure functions (issue_stats/release_notes_stats/docs_stats) + text and --markdown renderers; counts change entries, not raw bullets (skips TOC links and dependency bumps).
- Options considered: Shell one-liners (wc/find); raw bullet counts; a notebook.
- Why this one: Pure functions are unit-testable on a fake corpus; counting raw "- " lines inflated 1.33–1.37 entries 4,860 → real 3,327 (TOC + go.mod bumps). Also surfaces noise early: bot comments, incomplete comment threads, fix PRs without a release milestone (multi-hop gap).
- Would change if: Phase 2 chunk counts disagree with these numbers → stats should read processed chunks instead.
- Measured: release notes 3,327 entries (5 minors); docs 7,989 pages; issues pending fetch.

## D-007: Phase 0 learning pack + README setup/stats section
- Decision: docs/learning/phase-0.md (7 sections, 11 interview Qs) and README with setup and a corpus-stats table generated by `corpus_stats.py --markdown`.
- Options considered: Hand-written stats in README vs. generated table.
- Why this one: Generated table can't drift from the data; the gate requires stats in README.
- Would change if: n/a
- Measured: n/a
