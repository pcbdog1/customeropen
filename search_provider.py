from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass, field

import requests

from env_config import load_project_env
from pcb_leads.search import SearchResult


SEARCH_ORDER = ("brave", "tavily", "exa", "firecrawl")


@dataclass
class ProviderUsage:
    requests: int = 0
    new_domains: int = 0
    emails: int = 0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "requests": self.requests,
            "new_domains": self.new_domains,
            "emails": self.emails,
            "failure_reason": self.failures[-1] if self.failures else "",
        }


class StableSearchProvider:
    """Official API search chain; no HTML search-engine scraping or bypasses."""

    def __init__(self, provider: str | None = None) -> None:
        load_project_env()
        requested = (provider or os.getenv("SEARCH_PROVIDER", "auto")).strip().casefold()
        self.provider = requested or "auto"
        self.keys = {
            "brave": os.getenv("BRAVE_SEARCH_API_KEY", "").strip(),
            "tavily": os.getenv("TAVILY_API_KEY", "").strip(),
            "exa": os.getenv("EXA_API_KEY", "").strip(),
            "firecrawl": os.getenv("FIRECRAWL_API_KEY", "").strip(),
        }
        self.usage = {name: ProviderUsage() for name in SEARCH_ORDER}
        self._provider_attempts = 0
        self._provider_failures = 0

    @property
    def configured(self) -> bool:
        return any(self.keys[name] for name in SEARCH_ORDER[:3])

    @property
    def provider_attempts(self) -> int:
        return self._provider_attempts

    @property
    def provider_failures(self) -> int:
        return self._provider_failures

    @property
    def provider_diagnostics(self) -> list[dict[str, object]]:
        return [
            {"name": name, "attempts": usage.requests, "failures": len(usage.failures), "circuit_open": False, "last_error": usage.failures[-1] if usage.failures else ""}
            for name, usage in self.usage.items()
        ]

    def status(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            **{f"{name}_configured": bool(key) for name, key in self.keys.items()},
            "usage": self.usage_summary(),
        }

    def usage_summary(self) -> dict[str, dict[str, object]]:
        return {name: usage.as_dict() for name, usage in self.usage.items()}

    def record_email_found(self, provider: str) -> None:
        if provider in self.usage:
            self.usage[provider].emails += 1

    def _providers(self, include_firecrawl: bool = False) -> tuple[str, ...]:
        if self.provider not in {"auto", "local_seed"}:
            return (self.provider,) if self.provider in self.keys else ()
        names = SEARCH_ORDER if include_firecrawl else SEARCH_ORDER[:3]
        return tuple(name for name in names if self.keys[name])

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Use the next provider only when the prior provider errors or yields no records."""
        for name in self._providers():
            results = self._request(name, query, limit)
            if results:
                return results
        return []

    def search_all(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Diagnostic/domain-collection mode: test every configured discovery API once."""
        deduped: OrderedDict[str, SearchResult] = OrderedDict()
        for name in self._providers():
            for result in self._request(name, query, limit):
                deduped.setdefault(result.url.rstrip("/").casefold(), result)
        return list(deduped.values())

    def _request(self, name: str, query: str, limit: int) -> list[SearchResult]:
        self._provider_attempts += 1
        self.usage[name].requests += 1
        try:
            if name == "brave":
                response = requests.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": min(20, max(1, limit))}, headers={"Accept": "application/json", "X-Subscription-Token": self.keys[name]}, timeout=20)
                response.raise_for_status()
                records = response.json().get("web", {}).get("results", [])
                return self._results(name, query, records, "description")
            if name == "tavily":
                response = requests.post("https://api.tavily.com/search", json={"api_key": self.keys[name], "query": query, "max_results": limit}, timeout=20)
                response.raise_for_status()
                return self._results(name, query, response.json().get("results", []), "content")
            if name == "exa":
                response = requests.post("https://api.exa.ai/search", headers={"x-api-key": self.keys[name]}, json={"query": query, "numResults": limit, "contents": {"text": True}}, timeout=20)
                response.raise_for_status()
                return self._results(name, query, response.json().get("results", []), "text")
            if name == "firecrawl":
                response = requests.post("https://api.firecrawl.dev/v1/search", headers={"Authorization": f"Bearer {self.keys[name]}"}, json={"query": query, "limit": limit}, timeout=20)
                response.raise_for_status()
                return self._results(name, query, response.json().get("data", []), "description")
        except (requests.RequestException, ValueError, TypeError) as exc:
            self._provider_failures += 1
            self.usage[name].failures.append(f"{type(exc).__name__}: {str(exc)[:180]}")
        return []

    def _results(self, name: str, query: str, records: list[dict[str, object]], text_field: str) -> list[SearchResult]:
        results = [
            SearchResult(str(item.get("title", "")), str(item.get("url", "")), str(item.get(text_field, "")), f"[{name}] {query}")
            for item in records if item.get("url")
        ]
        self.usage[name].new_domains += len({item.url.rstrip("/").casefold() for item in results})
        return results
