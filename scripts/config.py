"""Runtime settings from environment variables / .env (pydantic-settings).

Corpus scope lives in data/manifest.yaml; connection details and tunables live here.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from manifest import REPO_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    postgres_user: str = "footnote"
    postgres_password: SecretStr = SecretStr("footnote")
    postgres_db: str = "footnote"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # chunks.embedding dimension. 384 = BAAI/bge-small-en-v1.5 (provisional, D-012);
    # Phase 2 picks the model and may migrate the column.
    embedding_dim: int = 384
    # Rows per COPY batch when loading (memory vs. round trips).
    load_batch_rows: int = 50_000
    # Used ONLY by corpus_stats.py to estimate chunk counts before Phase 2 chunking exists:
    # a review longer than this many characters is assumed to split into several chunks.
    review_chunk_chars: int = 1200


def get_settings() -> Settings:
    return Settings()
