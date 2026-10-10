"""Minimal GitHub GraphQL client that respects rate limits.

Three kinds of limits, three responses:
- Primary budget (5,000 points/hour): read x-ratelimit-remaining after every call and sleep
  until x-ratelimit-reset when it drops below `min_remaining_points`.
- Secondary limits (403/429, often with Retry-After): sleep Retry-After, the primary reset,
  or a fixed wait, then retry.
- Server errors / timeouts (5xx, network): exponential backoff, then raise GitHubServerError
  so the caller can shrink the request (heavy queries are what time out).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from settings import Settings

log = logging.getLogger(__name__)


class MissingTokenError(RuntimeError):
    """GITHUB_TOKEN is not set; GitHub GraphQL refuses anonymous requests."""


class GitHubServerError(RuntimeError):
    """GitHub kept failing with 5xx/timeouts after all retries."""


class GraphQLError(RuntimeError):
    """GitHub answered, but the query itself was rejected."""


def seconds_until_reset(headers: Mapping[str, str], now: float) -> float | None:
    """Seconds until the primary rate-limit window resets, from x-ratelimit-reset (epoch s)."""
    reset = headers.get("x-ratelimit-reset")
    if reset is None:
        return None
    return max(0.0, float(reset) - now) + 1.0  # +1s so we wake after the reset, not before


def rate_limit_wait(
    status: int, headers: Mapping[str, str], now: float, fallback_s: float
) -> float | None:
    """How long to wait before retrying a rate-limited response, or None if not rate-limited.

    Order follows GitHub's docs: Retry-After first, then the primary reset when the budget is
    exhausted, then a fixed wait for secondary limits that give no hint.
    """
    if status not in (403, 429):
        return None
    if "retry-after" in headers:
        return float(headers["retry-after"])
    if headers.get("x-ratelimit-remaining") == "0":
        return seconds_until_reset(headers, now) or fallback_s
    # 429, or a 403 with budget left and no hint: a secondary limit.
    return fallback_s


class GitHubClient:
    """Sends GraphQL queries; sleeps and retries around GitHub's rate limits."""

    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep) -> None:
        if settings.github_token is None or not settings.github_token.get_secret_value():
            raise MissingTokenError(
                "GITHUB_TOKEN is not set. Create a token (public repo read access is enough), "
                "put GITHUB_TOKEN=... in .env (see .env.example), and re-run."
            )
        self.settings = settings
        self._sleep = sleep
        self._http = httpx.Client(
            timeout=settings.request_timeout_s,
            headers={
                "Authorization": f"Bearer {settings.github_token.get_secret_value()}",
                "User-Agent": "footnote-corpus-fetcher",
            },
        )
        self.remaining: int | None = None  # last seen primary budget, for progress logs

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Run one GraphQL query and return its `data`. Retries rate limits and 5xx."""
        s = self.settings
        server_failures = 0
        for _attempt in range(s.max_retries + 1):
            try:
                resp = self._http.post(
                    s.github_graphql_url, json={"query": query, "variables": variables}
                )
            except httpx.TransportError as exc:
                server_failures += 1
                self._backoff_or_raise(server_failures, f"network error: {exc}")
                continue

            wait = rate_limit_wait(
                resp.status_code, resp.headers, time.time(), s.secondary_limit_wait_s
            )
            if wait is not None:
                log.warning("rate limited (HTTP %s); sleeping %.0fs", resp.status_code, wait)
                self._sleep(wait)
                continue
            if resp.status_code >= 500:
                server_failures += 1
                self._backoff_or_raise(server_failures, f"HTTP {resp.status_code}")
                continue
            if resp.status_code == 401:
                raise MissingTokenError("GitHub rejected GITHUB_TOKEN (401). Check the token.")
            resp.raise_for_status()

            body = resp.json()
            errors = body.get("errors") or []
            if any(e.get("type") == "RATE_LIMITED" for e in errors):
                wait = seconds_until_reset(resp.headers, time.time()) or s.secondary_limit_wait_s
                log.warning("GraphQL RATE_LIMITED; sleeping %.0fs", wait)
                self._sleep(wait)
                continue
            if errors:
                raise GraphQLError("; ".join(e.get("message", str(e)) for e in errors))

            self._respect_budget(resp.headers)
            return body["data"]
        raise GitHubServerError(f"gave up after {s.max_retries + 1} attempts")

    def _backoff_or_raise(self, failures: int, reason: str) -> None:
        if failures > self.settings.max_server_retries:
            raise GitHubServerError(reason)
        wait = self.settings.backoff_base_s**failures
        log.warning("%s; retry %d in %.0fs", reason, failures, wait)
        self._sleep(wait)

    def _respect_budget(self, headers: Mapping[str, str]) -> None:
        """Pause proactively when the hourly budget is nearly spent."""
        remaining = headers.get("x-ratelimit-remaining")
        if remaining is None:
            return
        self.remaining = int(remaining)
        if self.remaining < self.settings.min_remaining_points:
            wait = seconds_until_reset(headers, time.time()) or self.settings.secondary_limit_wait_s
            log.info("budget low (%d points left); sleeping %.0fs", self.remaining, wait)
            self._sleep(wait)
