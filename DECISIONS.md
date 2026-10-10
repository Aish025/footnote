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
