-- Enable pgvector. Tables and the HNSW index are created in Phase 2, not here.
CREATE EXTENSION IF NOT EXISTS vector;
