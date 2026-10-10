# Footnote — project context for Claude Code

## What this is
Footnote is a production-grade RAG assistant for on-call engineers. An engineer pastes an
error or describes a failure; Footnote finds similar past incidents, the fix that resolved
them, and the relevant docs/release notes for their version — every claim cited — and
says "I don't know" when the evidence is weak.

Business impact: shorter outages (lower MTTR) and less re-solving of known failures.
Validated problem: PagerDuty, Elastic and others ship similar features in production.

Corpus (v1): Kubernetes (kubernetes/kubernetes)
- GitHub issues (closed and open): title, body, comments, labels, affected version, state
- Pull requests linked to closed issues (the fix) — ground truth for "how was it fixed"
- CHANGELOG / release notes per version
- Official docs (kubernetes/website), versioned

Why it's hard (and what the project must prove):
- Multi-hop: issue → linked fix PR → release that shipped it → docs for that version.
- Version awareness: a fix in 1.29 doesn't apply to 1.27; answers must respect the user's version.
- Near-duplicates: thousands of similar-looking errors; the right one must rank in the top 5.
- Noisy text: stack traces, logs, code blocks, bot comments, "+1" comments.
- Secrets in logs: tokens/IPs/hostnames must be redacted before indexing and replies.
- Scale: quality must hold from thousands to millions of records (see Scale stages).

The owner is learning AI engineering through this build and must be able to explain
every decision and code path in interviews. Optimise for clarity over cleverness.

## Hard rules
1. Work on the CURRENT PHASE only (see bottom). Never jump ahead or build future phases.
2. AUTO-BUILD MODE: complete the whole current phase in one session without stopping for
   approval, in small steps — one git commit per step with a clear message. Keep modules
   small and readable. Stop and ask only for: missing API keys/tokens, rate limits that
   block progress, or anything that would break a hard rule below.
3. NEVER create, edit or generate content for `eval/golden_*.jsonl`. The human writes and
   verifies the golden set. You may write code that READS it, and tools that help the
   human browse candidate issues — but never choose questions or write answers.
4. Citations come from chunk metadata (issue/PR number, comment id, doc path + version),
   never from the model. Validate every citation in code against the chunks retrieved.
5. Refusal is one exact sentence (defined in config) so code and evals can detect it.
6. Evidence priority: merged fix PR > official docs/release notes for the user's version >
   maintainer comments > other comments. Bot and "+1" comments are excluded.
7. Raw data is never committed. data/raw/ and data/processed/ are gitignored; the fetch
   scripts + a manifest make the corpus reproducible.
8. Redact secrets (tokens, keys, IPs, emails, hostnames) before indexing and before replies.
9. Before adding a new library, explain in one line why it is needed. Keep deps minimal.
10. Never commit secrets. Tokens come from environment variables (.env, gitignored).

## Decision log (required)
After each step, append an entry to DECISIONS.md, max 10 lines:

    ## D-00N: <title>
    - Decision:
    - Options considered:
    - Why this one:
    - Would change if: <the metric/result that would prove it wrong>
    - Measured: <fill after eval, or "pending">

Seed decision D-001: PostgreSQL + pgvector as the single store (text, metadata, vectors).
Options: in-memory NumPy, Chroma, Qdrant. Why: data is relational (issue→PR→release→docs)
and metadata filtering (labels, version, state) is core. Would change if: p95 latency or
recall at 1M–10M chunks breaks the budget → compare against Qdrant in v3.

## Phase learning pack (required at the end of every phase)
Write `docs/learning/phase-N.md` so the owner can learn the phase and defend it in
interviews. Plain language, no hand-waving:
1. What was built — components and files, one line each.
2. How data flows — step by step, input to output, naming the functions/files.
3. Why — every decision with options considered and why this one won (link D-00N).
4. Trade-offs and limits — what this phase does badly, and what would fix it.
5. Numbers — measured results (or "pending eval").
6. Interview questions — 8–12 questions with short model answers grounded in THIS code
   and THESE numbers.
7. Try it yourself — 3 small exercises (run X, break Y, explain Z).

## Engineering standards (production-grade)
- Python 3.11, type hints everywhere, Pydantic models for all data passed between modules.
- Config via pydantic-settings; no magic numbers in code (chunk size, k, thresholds → config).
- Structured logging (one JSON log line per request: question, chunk ids, latency, tokens, cost).
- pytest for unit tests on pure logic (chunking, redaction, fusion, citation validation, refusal).
- ruff for lint/format. Small functions, clear names, docstrings on public functions.
- Every retrieval/answer change must be measurable by `eval/run.py`; results saved to
  `eval/results/<date>_<change>.json` with code version, index version, prompt version, model.
- Design for scale from v1: vector store behind an interface, batch + resumable ingestion
  (checkpoints, idempotent upserts), stable ids for issues/comments/chunks across re-indexing.

## Default stack (change only with a DECISIONS.md entry)
- Data: GitHub REST/GraphQL API (token in .env), with rate-limit handling and caching
- Store: PostgreSQL + pgvector (Docker Compose locally); HNSW index
- Chunking: structure-aware — issue body, each comment, PR description, each release-note
  entry, each docs section; code blocks/stack traces kept intact
- Embeddings: open model (e.g. BAAI/bge-small or bge-m3); keyword: Postgres full-text or BM25
- Fusion: reciprocal rank fusion (RRF); reranker: cross-encoder (Phase 3)
- LLM calls: through LiteLLM from day one (multi-provider incl. Gemini, cost tracking)
- API: FastAPI with streaming; tracing: Langfuse; container: Docker; CI: GitHub Actions

## Target repo structure (create folders only when their phase needs them)
    footnote/
    ├── CLAUDE.md, README.md, DECISIONS.md
    ├── data/manifest.yaml (what to fetch), data/raw/ and data/processed/ (gitignored)
    ├── scripts/ (fetch_issues.py, fetch_docs.py, corpus_stats.py)
    ├── eval/golden_v0.jsonl, eval/run.py, eval/results/
    ├── src/footnote/{ingest,retrieve,answer,api}/, config.py
    ├── tests/
    ├── docker-compose.yml, Dockerfile
    └── .github/workflows/ci.yml

## Roadmap (each phase has a "done when" gate)
- Phase 0 — Setup & corpus. Fetch scripts for issues (+comments, labels, linked PRs),
  release notes and versioned docs into data/raw; corpus_stats.py prints counts.
  Done when: ~5K recent closed issues with linked fixes + matching docs/release notes are
  fetched reproducibly, and stats are in the README.
- Phase 1 — Golden set v0 (HUMAN ONLY): 30–40 questions across categories: similar_incident,
  fix_lookup, version_specific, multi_hop (issue→fix→release→docs), should_refuse.
  3 numeric targets in README.
- Phase 2 — Walking skeleton: normalise → redact → chunk → embed → store in pgvector →
  dense retrieval → LLM answer with citations; eval/run.py prints recall@5, MRR and answer
  metrics per category. Done when: one command produces the baseline row of results.
- Phase 3 — Retrieval upgrades ONE AT A TIME, each a results row: keyword + RRF hybrid,
  reranker, metadata filters (version/labels/state), contextual chunk prefixes,
  query rewriting (raw vs HyDE vs multi-query), error-message normalisation.
- Phase 4 — Answer safety: citation validation, exact refusal, version-aware answering,
  evidence priority, grounding check, secret redaction verified by tests.
- Phase 5 — Agentic investigation, ONLY if Phase 4 eval shows single-pass failing on
  multi_hop. Tool-using agent (LangGraph) over the SAME retrieval layer, tools:
  search_incidents(query, version, labels), get_fix_pr(issue_number),
  get_release_notes(version), get_docs(topic, version). Step/budget limits, typed tool
  outputs, trace per run. A router sends simple questions to the pipeline and multi-hop
  ones to the agent. Report pipeline vs agent vs router on accuracy, p95 latency and cost.
- Phase 6 — Eval rigor: LLM judge calibrated vs human labels (Cohen's kappa), position/
  length/self-preference bias numbers, CI gate failing a PR on regression.
- Phase 7 — Production service: FastAPI + streaming, config, structured logs, Langfuse,
  LiteLLM retries/fallbacks/cost caps, rate limits, Docker, tests, CI.
- Phase 8 — Ship v1: deploy (Cloud Run), 90-sec demo, README opens with results table,
  honest failure notes, cost and latency per query.

## Scale stages (after Phase 8 — do NOT build early)
- v1 — ~5K Kubernetes issues + docs + release notes (Phases 0–8).
- v2 — Full Kubernetes issue/PR history + related CNCF projects → 100K+ issues (millions of
  chunks). Expect and fix: API rate limits, ingestion throughput, dedup, noisy text,
  relevance drift, re-index cost — each fix measured against the golden set.
- v3 — Stress: 1M → 10M chunks (more repos / public Q&A dumps). Measure recall@5 and p95
  latency at 10K/100K/1M/10M; concurrent load tests; update/delete propagation; recovery
  after failure; cost per 1,000 queries; pgvector vs Qdrant comparison.
  Goal: prove answer quality holds as the index grows, not just that it's fast.

## Full feature scope (reference only — build each item in its phase, never early)
- Ingestion: incremental sync of new/updated issues (since-timestamp), re-index + re-run
  eval on change; bot/noise filtering; stack-trace-aware parsing.
- Retrieval: dense; keyword + RRF; reranker; metadata filters; contextual prefixes;
  query rewriting; follow-up questions rewritten to standalone; retrieval ablation table.
- Answering: exact refusal; citation validation; version-aware answers; evidence priority;
  corrective RAG (relevance grading → retry or escalate); agentic multi-hop if justified.
- Routing: draft → grader from a different model vendor → deterministic routing
  (answer / human review / escalate) using calibrated confidence.
- Evaluation: per-category metrics vs dense-only baseline; RAGAS; calibrated LLM judge
  (kappa, self-agreement ceiling, anchored 3–5 point scales, reason before score, no tuning
  on the reported set); three bias numbers; hallucination audit (wrong fix, wrong version,
  invented citation, stale answer); A/B significance test cheap vs expensive model;
  context-size study (quality vs cost); CI gate shown failing on a bad PR.
- Guardrails: secret redaction; untrusted text treated as data, never instructions;
  poisoned-issue red-team case (an issue containing injected instructions).
- Ops: LiteLLM gateway (incl. Gemini, retries, fallbacks, budget caps, caching keyed on
  prompt + index version); Langfuse tracing + prompt versioning; FastAPI + SSE, auth,
  rate limits; structured logs; Docker; GitHub Actions.
- Later (v2): per-team access control (filter by team/permission before ranking).
- Ship: README = results table first, then honest failure notes, architecture, worked
  cost per query, p50/p95 latency, related work (PagerDuty, Elastic, RecallOps), setup last.

## Phase 1 note
Phase 1 (golden set) is human-only. While the owner writes it, you may build Phase 2's
pipeline, but `eval/run.py` must read `eval/golden_v0.jsonl` and never generate questions.

## CURRENT PHASE
Phase 0 — Setup & corpus (incident version). The earlier airline files (docs/raw,
docs/sources.csv, scripts for airline docs) are obsolete: remove them in the first commit.
Then: docker-compose for Postgres+pgvector, data/manifest.yaml, scripts/fetch_issues.py
(GitHub API, ~5K recent closed issues with linked PRs, resumable, rate-limit aware),
scripts/fetch_docs.py (release notes + versioned docs), scripts/corpus_stats.py,
.gitattributes, D-001 entry, learning pack docs/learning/phase-0.md.
(Owner updates this line when a phase's gate is met.)
