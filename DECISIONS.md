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

## D-009: Download Amazon Reviews 2023 (Cell_Phones_and_Accessories) gzipped from the McAuley Lab host
- Decision: data/manifest.yaml lists 4 files (meta 858 MB, reviews 2.53 GB, Q&A 2016 7 MB, Q&A 2019 18.5 MB) with expected bytes; download_data.py streams to .part, resumes via HTTP Range, rejects wrong sizes, records sha256 in data/raw/_downloads.json.
- Options considered: Hugging Face `datasets` (adds a heavy dep + cache; same files uncompressed 13.3 GB); HF direct .jsonl (9.3 + 4.0 GB); lab-hosted .jsonl.gz (3.4 GB).
- Why this one: Official source, 4× smaller, streamed and gunzipped on the fly later (no 13 GB on disk); no new dependency (httpx already present). Range resume tested: truncated file → 206 → identical sha256.
- Would change if: The lab host disappears or throttles below ~1 MB/s → switch URLs to the HF mirror (same content).
- Measured: ~2.4 MB/s → ~25 min for the full set; Q&A files 13 s / 9 s.
