# Phase 0 — Setup & corpus (learning pack)

Goal of the phase: a reproducible, measured corpus in Postgres that every later phase
(retrieval, aggregation, evaluation) can trust. No retrieval or LLM code yet.

---

## 1. What was built

| File | What it does |
|---|---|
| `docker-compose.yml`, `db/init/01_extensions.sql` | One Postgres 17 + pgvector container; enables the `vector` extension. |
| `.env.example`, `scripts/config.py` | Connection settings and tunables (`embedding_dim`, `load_batch_rows`, `review_chunk_chars`) via pydantic-settings. |
| `data/manifest.yaml`, `scripts/manifest.py` | The corpus definition: 4 source files with URLs and expected byte sizes, the v1 subset rule, the Q&A threshold. Validated into Pydantic models. |
| `scripts/download_data.py` | Streams each file to `.part`, resumes with HTTP Range, checks size, records sha256 in `data/raw/_downloads.json`. |
| `scripts/select_subset.py` | Applies the subset rule in three streaming passes over the gzipped files; writes `products.jsonl`, `reviews.jsonl`, `subset_report.json`. |
| `scripts/check_qa_overlap.py` | Matches the older Q&A datasets to v1 products by ASIN; writes `qa_overlap.json` and, if enough products match, `qa.jsonl`. |
| `db/schema.sql`, `scripts/db.py` | Tables `products`, `reviews`, `qa`, `chunks` (empty until Phase 2); connect + apply schema. |
| `scripts/load_db.py` | Parses JSONL into typed rows, drops duplicate keys, COPYs into a staging table, upserts. Idempotent. |
| `scripts/corpus_stats.py` | Every corpus figure as a SQL query; prints a report or a README table. |
| `tests/test_*.py` | 26 unit tests on the pure logic (range resume, bucket rule, ordering, COPY escaping, price parsing, Q&A parsing, stats rendering). |

## 2. How data flows

1. **Download** — `download_data.py: main → download(spec) → stream_once`.
   Sends `Range: bytes=<existing>-` if a `.part` exists; `resume_offset` turns 206 into
   "append" and 200 into "start over". On finish, size must equal the manifest's `bytes`,
   then `sha256_of` is recorded. Output: `data/raw/*.gz` (3.4 GB).
2. **Select** — `select_subset.py: main`.
   - Pass 1 `find_candidates` reads metadata: `bucket_for` (category path ∩ bucket
     names) and `has_listing_text` (title + features or description).
   - Pass 2 `count_reviews` counts reviews per candidate. It pulls `parent_asin` with a
     regex instead of parsing 20.8M JSON lines — much faster.
   - `choose` keeps products with 50–2,000 reviews, sorts each bucket by
     `order_key = sha256(salt:parent_asin)`, takes the first 1,000.
   - `write_products` / `write_reviews` copy the chosen rows to `data/processed/`.
3. **Q&A overlap** — `check_qa_overlap.py: main`. Builds an ASIN → product map from
   parent ASINs *and* variant ASINs seen in reviews (`asin_to_product`), parses each Q&A
   line with `ast.literal_eval` (the files are Python literals, not JSON — literal_eval
   cannot run code), normalises both formats with `qa_pairs`, and writes `qa.jsonl` only
   if ≥ 50 products match.
4. **Load** — `load_db.py: main → apply_schema → load_table` per table.
   `product_row` / `review_row` build Pydantic rows (`review_id = stable_id(user, parent,
   asin, timestamp)` because the dataset has no review id). `dedupe` drops repeated keys.
   `copy_line` escapes rows into COPY text format; batches of 50,000 go into a TEMP
   `stage` table; `UPSERT_SQL` does `INSERT … ON CONFLICT DO UPDATE`. Each table is one
   transaction: it either fully loads or rolls back.
5. **Stats** — `corpus_stats.py: collect` runs fixed SQL queries (`SQL_REVIEWS`,
   `SQL_PER_PRODUCT`, `SQL_EST_CHUNKS`, …) into a `Stats` model; `render_text` /
   `render_markdown` only format.

## 3. Why (decisions)

- **One store: Postgres + pgvector** ([D-001](../../DECISIONS.md)) — products, reviews and
  Q&A are relational; counts must come from SQL (hard rule 5), so aggregates live next to
  the evidence. Alternative: a vector DB + separate SQL store = two sources of truth.
- **Data and secrets out of git** (D-002) — the corpus is GBs and contains personal text.
  The manifest + scripts are the reproducible record.
- **Lab-hosted .jsonl.gz, streamed** (D-009) — 4× smaller than the HF copy, no extra
  dependency, read gzipped on the fly. Range resume makes a 25-minute download safe.
- **Subset rule** (D-010) — buckets by category path, listing text required (a product
  with no listing can't support listing_claim or conflict questions), 50–2,000 reviews
  (enough for patterns, no mega-products), hash order (reproducible, not biased toward
  popular items). Every product keeps **all** its reviews — capping reviews would make
  SQL counts wrong.
- **pg8000 + COPY + staging upsert** (D-011) — the compiled drivers are blocked on the
  dev machine; pg8000 is pure Python and supports COPY. Staging + `ON CONFLICT` makes
  re-runs idempotent and keeps ids stable for re-indexing.
- **vector(384), no HNSW yet** (D-012) — sized for bge-small; Phase 2 decides.
- **Stats in SQL** (D-013) — one source of truth; the stats double as a load check.
- **Q&A used, but small** (D-014) — exact ASIN match is verifiable; 59 products pass the
  threshold of 50.

## 4. Trade-offs and limits

- **Q&A coverage is thin**: 59 of 2,816 products (2.1%). `qa_answer` evidence will be
  rare; most compatibility questions must be answered from listings + reviews.
- **Q&A cross-source duplicates**: 290 pairs appear in both the 2016 and 2019 files.
  Fix: dedup on normalised (product, question, answer) in Phase 3.
- **"Exact duplicate" means same key, not same text**: review_id hashes user, product,
  variant and timestamp. 6,981 records (1.1%) collided and the first was kept. If two
  genuinely different reviews shared all four fields, one was lost. Fix: include a text
  hash in the key and count how many collisions differ in text.
- **Near-duplicate and repeat reviews remain**: 514 user/product pairs have >1 review;
  near-duplicate text isn't detected yet. Aggregates must dedup (Phase 3/4).
- **No earbuds bucket**: this category file has no earbuds path; single-ear headsets is
  the closest. Earbud comparison questions will be limited.
- **Small buckets**: cables (584) and headsets (232) are limited by the review band, not
  the cap.
- **Noise**: 11.4% of reviews are under 20 characters; 64.8% are 5-star (positivity skew).
- **PII is not yet redacted**: review text is stored as-is in Postgres. Hard rule 8
  requires redaction before indexing/replies — that happens in the Phase 2 chunk step.
- **Chunk count is an estimate** (639,850) from a character heuristic, not real chunking.
- **Single-threaded load**: 37 s for 623K reviews is fine for v1; v2 (millions) may need
  parallel COPY or a binary driver in Docker.

## 5. Numbers

| Measure | Value |
|---|---:|
| Raw download | 3.4 GB, ~25 min at ~2.4 MB/s, sha256 recorded |
| Source size | 1,288,490 products, 20.8M review lines |
| Selected | 2,816 products (cables 584, cases 1,000, chargers 1,000, headsets 232) |
| Reviews loaded | 623,218 (6,981 duplicate keys dropped) |
| Date range | 2004-12-13 to 2023-09-11 |
| Verified purchase | 94.4% |
| Reviews per product | min 46, p25 70, median 116, p90 515.5, max 1,975 |
| Ratings | 1★ 13.0%, 2★ 5.4%, 3★ 6.4%, 4★ 10.4%, 5★ 64.8% |
| Listing fields present | features 98.2%, description 42.7%, details 99.7%, price 52.8% |
| Q&A | 2,371 pairs for 59 products (2,081 distinct) |
| Estimated chunks | 639,850 (listing 6,827, review 630,652, qa 2,371) |
| Load time | 37 s first run; 45 s re-run, identical counts |
| Q&A overlap check | 14 s |
| Tests | 26 passed; ruff clean |

Retrieval and answer metrics: pending (Phase 2 eval).

## 6. Interview questions

1. **Why Postgres + pgvector instead of a vector database?**
   Counts must come from SQL, and the evidence is relational (product → reviews → Q&A).
   One store means filters, aggregates and vectors share one source of truth. I'd
   revisit with Qdrant only if p95 latency or recall breaks at 1M–10M chunks (D-001).
2. **How did you choose 2,816 products out of 1.29 million?**
   A rule in the manifest: 4 category buckets, listing text required, 50–2,000 reviews,
   up to 1,000 per bucket in sha256(salt + id) order. `subset_report.json` records the
   funnel at each step, e.g. cases 525,158 → 383,126 → 19,606 → 1,000.
3. **Why hash order rather than random sampling or top-N by popularity?**
   Top-N biases toward mega-products. Hash order is as unbiased as random, but each
   product's position depends only on its own id — adding or removing other candidates
   doesn't reshuffle the selection.
4. **Why not cap reviews per product to save space?**
   A capped product would give wrong answers to "how many reviews mention X?". Keeping all
   reviews means SQL counts are true for the stored data.
5. **Why does the minimum show 46 reviews when the floor is 50?**
   Selection counted raw lines; the load then dropped 6,981 duplicate review keys (1.1%).
   Some products fell below 50 after dedup. The stats come from the database, so they
   show what retrieval will actually see.
6. **How is the load idempotent?**
   Rows go to a TEMP staging table via COPY, then `INSERT … ON CONFLICT (pk) DO UPDATE`.
   Ids are deterministic hashes, so re-running yields the same rows. Measured: re-run
   gave identical counts (2,816 / 623,218 / 2,371).
7. **The dataset has no review id — how do citations stay stable?**
   `review_id = sha256(user_id|parent_asin|asin|timestamp)[:24]`. Same input gives the
   same id across re-loads, which Phase 2 chunk ids and Phase 4 citation checks rely on.
8. **Why COPY instead of INSERT?**
   COPY streams many rows per round trip; 623K reviews load in seconds instead of minutes.
   The cost is escaping tabs, newlines, backslashes and NULs myself (`copy_field`), which
   is unit-tested.
9. **How did you decide whether to use Q&A?**
   Exact ASIN match on parent + variant ASINs, with a threshold of 50 products set in
   the manifest. 59 matched (2.1%), so it's included but marginal. Both files match
   the same 63 ASINs, and 290 pairs are duplicated across them.
10. **Why are the Q&A files parsed with ast.literal_eval — isn't that dangerous?**
    The files are Python dict literals, not JSON. `literal_eval` only evaluates literals
    (strings, numbers, dicts, lists) and raises on anything else; it never calls `eval`.
11. **What noise did you find, and what will you do about it?**
    11.4% of reviews are under 20 characters; 514 repeat reviewer pairs; 64.8% are
    5-star. Aggregates will dedup same-user reviews and report verified-only counts.
    Short reviews may be down-weighted or filtered — measured on the golden set.
12. **How do you know the estimate of 639,850 chunks is reasonable?**
    It's a SQL heuristic: one chunk per listing field group, ceil(chars/1200) per review,
    one per Q&A pair. D-013 says to replace it with the real count if Phase 2 differs by
    more than 20%.

## 7. Try it yourself

1. **Run** — `python scripts/corpus_stats.py` then `python scripts/load_db.py` again, and
   re-run stats. Confirm the counts don't change. Then run, in psql:
   `SELECT product_id, COUNT(*) FROM reviews GROUP BY 1 ORDER BY 2 LIMIT 5;` and find a
   product below 50. Explain why it's there.
2. **Break** — edit `data/manifest.yaml` and set `qa.min_matched_products: 100`. Run
   `check_qa_overlap.py`: `qa.jsonl` should be deleted and `qa_used: false` recorded.
   Run `load_db.py` — note the `qa` table keeps its old rows (the loader only upserts,
   never deletes). Decide whether that is a bug, then restore the value.
3. **Explain** — in `select_subset.py`, `count_reviews` uses a regex instead of
   `json.loads`. Explain why it's safe here and one input where it would be wrong. (Hint:
   in each review line the `text` field comes *before* `parent_asin`, and `search`
   returns the first match. What if a review's text contained `"parent_asin": "X"`?
   Look at how JSON escapes quotes inside a string — and what a non-JSON writer could
   produce.)
