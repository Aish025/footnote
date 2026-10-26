-- Footnote schema (Phase 0). Applied by scripts/load_db.py; idempotent (IF NOT EXISTS).
-- {embedding_dim} is substituted from config (Settings.embedding_dim).
--
-- Evidence types (CLAUDE.md): listing_claim (products), review (reviews), qa_answer (qa),
-- aggregate (computed by SQL at query time, never stored as text).

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per product = Amazon parent_asin. Listing text is a CLAIM by the seller, not a spec.
CREATE TABLE IF NOT EXISTS products (
    product_id      TEXT PRIMARY KEY,          -- parent_asin (stable id for citations)
    subset_bucket   TEXT NOT NULL,             -- v1 selection bucket: chargers/cables/cases/headsets
    title           TEXT NOT NULL,
    main_category   TEXT,
    categories      TEXT[] NOT NULL,
    features        TEXT[] NOT NULL,           -- bullet points (listing_claim)
    description     TEXT[] NOT NULL,           -- paragraphs (listing_claim)
    details         JSONB NOT NULL,            -- key/value specs as listed (listing_claim)
    price           NUMERIC(10, 2),            -- NULL when missing or not a single number
    store           TEXT,
    average_rating  NUMERIC(3, 2),             -- as listed by Amazon, NOT recomputed
    rating_number   INTEGER                    -- as listed by Amazon (all variants, all time)
);

-- One row per customer review. The dataset has no review id: review_id is a stable hash of
-- (user_id, parent_asin, asin, timestamp), so exact duplicate rows collapse on load.
CREATE TABLE IF NOT EXISTS reviews (
    review_id          TEXT PRIMARY KEY,
    product_id         TEXT NOT NULL REFERENCES products (product_id),
    asin               TEXT NOT NULL,          -- variant (colour/size) the review was left on
    user_id            TEXT NOT NULL,          -- anonymised by the dataset; used for dedup
    rating             SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title              TEXT NOT NULL,
    text               TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL,
    verified_purchase  BOOLEAN NOT NULL,
    helpful_vote       INTEGER NOT NULL,
    has_images         BOOLEAN NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_product_idx ON reviews (product_id);
CREATE INDEX IF NOT EXISTS reviews_product_created_idx ON reviews (product_id, created_at);

-- Community Q&A (older McAuley datasets), only for products whose ASIN overlaps the subset.
CREATE TABLE IF NOT EXISTS qa (
    qa_id          TEXT PRIMARY KEY,           -- stable hash of (source, asin, question, answer)
    product_id     TEXT NOT NULL REFERENCES products (product_id),
    source         TEXT NOT NULL,              -- qa_2016 | qa_2019
    question       TEXT NOT NULL,
    answer         TEXT NOT NULL,
    question_type  TEXT,
    answered_on    TEXT                        -- as given (free-text date in the source)
);
CREATE INDEX IF NOT EXISTS qa_product_idx ON qa (product_id);

-- Retrieval units. Created now so the schema is complete; filled in Phase 2 (chunk + embed).
-- The HNSW index is created in Phase 2, once the embedding model is fixed.
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id       TEXT PRIMARY KEY,           -- stable: <source_id>:<field or part>
    product_id     TEXT NOT NULL REFERENCES products (product_id),
    evidence_type  TEXT NOT NULL CHECK (evidence_type IN ('listing_claim', 'review', 'qa_answer')),
    source_id      TEXT NOT NULL,              -- product_id | review_id | qa_id
    field          TEXT,                       -- listing field group or review part
    text           TEXT NOT NULL,
    embedding      vector({embedding_dim})
);
CREATE INDEX IF NOT EXISTS chunks_product_idx ON chunks (product_id);
