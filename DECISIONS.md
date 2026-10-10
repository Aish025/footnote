# Decisions

Scope history: airline policies → Kubernetes incidents → product evidence (current).
Entries marked **Superseded** applied only to the Kubernetes incident scope; they are kept
as a record of what was tried and why it no longer applies.

## D-001: PostgreSQL + pgvector as the single store (text, metadata, vectors)
- Decision: One Postgres 17 + pgvector container (docker-compose.yml, image pgvector/pgvector:pg17); extension enabled by db/init.
- Options considered: In-memory NumPy, Chroma, Qdrant, Postgres + pgvector.
- Why this one: Still applies, more strongly: products ↔ reviews ↔ Q&A are relational, metadata filters (product, verified, date, rating) are core, and hard rule 5 requires counts from SQL — the aggregates must live next to the evidence. HNSW available.
- Would change if: p95 latency or recall@5 at 1M–10M chunks breaks the budget → compare against Qdrant in v3.
- Measured: pending

## D-002: Corpus and secrets out of git; LF line endings everywhere
- Decision: .gitignore covers data/raw/, data/processed/, .env*; .gitattributes forces LF for code/data/SQL/YAML and marks binaries.
- Options considered: Commit a small sample corpus; Git LFS; rely on per-machine core.autocrlf.
- Why this one: Still applies (hard rules 7 and 11). The review corpus is GBs and contains PII; the manifest + scripts are the reproducible record. Windows dev vs. Linux Docker/CI.
- Would change if: The upstream dataset changes or disappears → snapshot data/raw to object storage with a version id.
- Measured: n/a (setup)

## D-003: Superseded — data/manifest.yaml for the Kubernetes corpus
- The idea (one validated manifest defines the corpus) carries over to D-009; the Kubernetes content (issue window, docs branches) is removed.

## D-004: Superseded — GitHub GraphQL issue fetch in date windows
- Kubernetes-only. Lesson kept: report a shortfall against a target honestly rather than loosening the filter (fetch ended at 2,799 of ~5K).

## D-005: Superseded — SHA-pinned release notes and sparse-clone docs
- Kubernetes-only. Lesson kept: pin and record the exact upstream version of every source.

## D-006: Superseded — Kubernetes corpus_stats.py
- Replaced by a SQL-based corpus_stats.py (hard rule 5: numbers come from the database).

## D-007: Superseded — Kubernetes learning pack and README stats
- Replaced by the product-evidence learning pack.

## D-008: Superseded — Kubernetes issue fetch result (2,799 issues, version gap)
- Kubernetes-only; data and scripts removed.
