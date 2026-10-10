# Decisions

## D-001: PostgreSQL + pgvector as the single store (text, metadata, vectors)
- Decision: One Postgres 17 + pgvector container (docker-compose.yml, image pgvector/pgvector:pg17); extension enabled by db/init; schema deferred to Phase 2.
- Options considered: In-memory NumPy, Chroma, Qdrant, Postgres + pgvector.
- Why this one: Data is relational (issue → fix PR → release → docs) and metadata filtering (labels, version, state) is core; one store means one source of truth, SQL joins and transactional upserts. HNSW available.
- Would change if: p95 latency or recall@5 at 1M–10M chunks breaks the budget → compare against Qdrant in v3.
- Measured: pending
