from __future__ import annotations

import os

import requests

from env_config import load_project_env
from pcb_leads.fetcher import FetchedPage


class FirecrawlFallbackFetcher:
    """Try Firecrawl only after the normal public official-page fetch fails."""

    def __init__(self, fallback, api_timeout: int = 25) -> None:
        load_project_env()
        self.fallback = fallback
        self.enabled = os.getenv("FIRECRAWL_ENABLED", "True").casefold() == "true"
        self.api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
        self.api_timeout = api_timeout
        self.requests = 0
        self.failures: list[str] = []

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key)

    def fetch(self, url: str):
        page = self.fallback.fetch(url)
        if page or not self.configured:
            return page
        self.requests += 1
        try:
            response = requests.post("https://api.firecrawl.dev/v1/scrape", headers={"Authorization": f"Bearer {self.api_key}"}, json={"url": url, "formats": ["html"]}, timeout=self.api_timeout)
            response.raise_for_status()
            data = response.json().get("data", {})
            html = str(data.get("html") or "")
            if html:
                return FetchedPage(url=str(data.get("metadata", {}).get("sourceURL") or url), html=html)
        except (requests.RequestException, ValueError, TypeError) as exc:
            self.failures.append(f"{type(exc).__name__}: {str(exc)[:180]}")
        return None

    def usage(self) -> dict[str, object]:
        return {"requests": self.requests, "new_domains": 0, "emails": 0, "failure_reason": self.failures[-1] if self.failures else ""}
