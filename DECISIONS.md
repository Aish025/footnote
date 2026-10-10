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

## D-010: v1 subset rule — 4 accessory buckets, 50–2,000 reviews, ≤1,000 per bucket, hash order
- Decision: Buckets matched on category path (chargers: Chargers & Power Adapters; cables: Cables & Adapters; cases: Basic/Flip Cases; headsets: Single Ear Bluetooth Headsets); require title + features/description; 50–2,000 reviews counted in the reviews file; up to 1,000 per bucket in sha256(salt+parent_asin) order. Rule lives in data/manifest.yaml; scripts/select_subset.py writes data/processed/ + subset_report.json (funnel).
- Options considered: Top-N by rating_number (popularity-biased, mega-products); random sample with seed (fine, but hash order is stable even if the candidate list changes); cap reviews per product (breaks SQL aggregate completeness).
- Why this one: Every selected product keeps ALL its reviews → counts are true for the stored data; 460K/1.29M products have no listing text, so they can't support listing_claim/conflict questions; hash order is reproducible and unbiased by popularity. No earbuds category exists in this file (Electronics holds them) → closest bucket is single-ear headsets.
- Would change if: Golden-set authors can't find enough conflict/compatibility questions in the subset, or a bucket ends up too small (funnel shows it) → adjust buckets/band in the manifest.
- Measured: pending (funnel recorded after the run).

## D-011: Schema (products, reviews, qa, chunks) loaded with pg8000 COPY + idempotent upserts
- Decision: db/schema.sql (IF NOT EXISTS); load_db.py parses JSONL into Pydantic rows, drops exact-duplicate keys (counted), COPYs batches into a temp staging table, upserts with ON CONFLICT DO UPDATE in one transaction per table. review_id = sha256(user_id|parent_asin|asin|timestamp)[:24] (dataset has no review id).
- Options considered: psycopg 3 (compiled DLL blocked by Windows Application Control on the dev machine — not bypassed); psycopg2 (same issue); SQLAlchemy/pandas to_sql (row INSERTs, heavy deps); pg8000 (pure Python, supports COPY).
- Why this one: pg8000 needs no native code; COPY loads ~10^6 rows in seconds; staging + upsert makes re-runs idempotent and keeps stable ids for re-indexing. Arrays/specs staged as JSONB and converted in SQL (no hand-built array literals).
- Would change if: Load time for v2 (millions of reviews) is dominated by Python-side escaping → psycopg binary in Docker/CI where DLL policy doesn't apply.
- Measured: pending (row counts after load).

## D-012: chunks.embedding is vector(384) — provisional
- Decision: Size the column for BAAI/bge-small-en-v1.5 (384-d, English) via Settings.embedding_dim; no HNSW index yet.
- Options considered: bge-m3 (1024-d, multilingual — not needed for English reviews); untyped `vector` (HNSW needs a fixed dimension).
- Why this one: Reviews are English; small model = cheaper embedding of ~10^6 chunks. Table is empty, so Phase 2 can change it with one ALTER.
- Would change if: Phase 2 eval shows a larger model improves recall@5 enough to justify cost.
- Measured: pending (Phase 2).

## D-013: corpus_stats.py computes every figure in SQL
- Decision: Fixed SQL queries over the loaded tables (products, reviews, qa, chunks); Python only formats. Chunk estimate = listing field groups + ceil(review chars / review_chunk_chars) + Q&A pairs, chunk size bound as a parameter from config.
- Options considered: Count from JSONL files in Python (fast to write, but a second source of truth); pandas.
- Why this one: Hard rule 5 — numbers come from the database. Stats double as a load check: they describe exactly what retrieval and aggregation will see. Also surfaces dedup risk (same user, same product, >1 review) and short-review noise early.
- Would change if: Phase 2 real chunk counts differ from the estimate by >20% → replace the estimate with SELECT COUNT(*) FROM chunks.
- Measured: pending (after load).
