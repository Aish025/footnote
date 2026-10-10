"""Runtime settings from environment variables / .env (pydantic-settings).

Secrets (GITHUB_TOKEN) and network tuning live here; corpus scope lives in data/manifest.yaml.
"""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from corpus import REPO_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    github_token: SecretStr | None = None
    github_graphql_url: str = "https://api.github.com/graphql"
    raw_content_url: str = "https://raw.githubusercontent.com"

    request_timeout_s: float = 60.0
    max_retries: int = 6
    backoff_base_s: float = 2.0  # exponential backoff: base ** attempt seconds
    # Pause before the primary limit is exhausted (GraphQL budget: 5,000 points/hour).
    min_remaining_points: int = 100
    # GitHub's secondary (abuse) limits often come without a Retry-After header.
    secondary_limit_wait_s: float = 60.0
    # Smallest page size we shrink to when GitHub times out on a heavy query.
    min_page_size: int = 5


def get_settings() -> Settings:
    return Settings()
