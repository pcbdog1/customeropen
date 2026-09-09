from __future__ import annotations

import os
import time
import base64
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from .utils import normalize_domain


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    query: str


class SearchProvider:
    def search(self, query: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError


class FallbackSearchProvider(SearchProvider):
    def __init__(self, primary: SearchProvider):
        self.primary = primary
        self._attempt_budget: int | None = None
        self._provider_attempts = 0
        self._provider_failures = 0

    def configure_attempt_budget(self, max_attempts: int) -> None:
        self._attempt_budget = max(0, int(max_attempts))
        self._provider_attempts = 0
        self._provider_failures = 0
        configure = getattr(self.primary, "configure_attempt_budget", None)
        if callable(configure):
            configure(self._attempt_budget)

    @property
    def provider_attempts(self) -> int:
        reported = getattr(self.primary, "provider_attempts", None)
        return int(reported) if reported is not None else self._provider_attempts

    @property
    def provider_failures(self) -> int:
        reported = getattr(self.primary, "provider_failures", None)
        return int(reported) if reported is not None else self._provider_failures

    @property
    def provider_diagnostics(self) -> list[dict[str, object]]:
        reported = getattr(self.primary, "provider_diagnostics", None)
        if reported is not None:
            return list(reported)
        return [
            {
                "name": type(self.primary).__name__,
                "attempts": self._provider_attempts,
                "failures": self._provider_failures,
                "circuit_open": False,
                "last_error": "",
            }
        ]

    def search(self, query: str, limit: int) -> list[SearchResult]:
        primary_reports_attempts = getattr(self.primary, "provider_attempts", None) is not None
        if (
            not primary_reports_attempts
            and self._attempt_budget is not None
            and self._provider_attempts >= self._attempt_budget
        ):
            return []
        if not primary_reports_attempts:
            self._provider_attempts += 1
        try:
            results = self.primary.search(query, limit)
        except Exception:
            if not primary_reports_attempts:
                self._provider_failures += 1
            results = []
        if results:
            return results
        if "germany" not in query.casefold():
            return []
        from .seed_sources import germany_seed_results

        return germany_seed_results(query, limit)


class MultiSearchProvider(SearchProvider):
    """Try configured providers in priority order for every query."""

    def __init__(self, providers: list[SearchProvider]):
        if not providers:
            raise ValueError("at least one search provider is required")
        self.providers = providers
        self._attempt_budget: int | None = None
        self._provider_attempts = 0
        self._provider_failures = 0
        self._attempts_by_provider = [0] * len(providers)
        self._failures_by_provider = [0] * len(providers)
        self._last_errors = [""] * len(providers)

    def configure_attempt_budget(self, max_attempts: int) -> None:
        self._attempt_budget = max(0, int(max_attempts))
        self._provider_attempts = 0
        self._provider_failures = 0
        self._attempts_by_provider = [0] * len(self.providers)
        self._failures_by_provider = [0] * len(self.providers)
        self._last_errors = [""] * len(self.providers)

    @property
    def provider_attempts(self) -> int:
        return self._provider_attempts

    @property
    def provider_failures(self) -> int:
        return self._provider_failures

    @property
    def provider_diagnostics(self) -> list[dict[str, object]]:
        return [
            {
                "name": f"{type(provider).__name__}[{index}]",
                "attempts": self._attempts_by_provider[index],
                "failures": self._failures_by_provider[index],
                "circuit_open": self._failures_by_provider[index] >= 2,
                "last_error": self._last_errors[index],
            }
            for index, provider in enumerate(self.providers)
        ]

    def search(self, query: str, limit: int) -> list[SearchResult]:
        for index, provider in enumerate(self.providers):
            if self._failures_by_provider[index] >= 2:
                continue
            if self._attempt_budget is not None and self._provider_attempts >= self._attempt_budget:
                break
            self._provider_attempts += 1
            self._attempts_by_provider[index] += 1
            try:
                results = provider.search(query, limit)
            except Exception as exc:
                self._provider_failures += 1
                self._failures_by_provider[index] += 1
                self._last_errors[index] = f"{type(exc).__name__}: {exc}"
                continue
            results = [result for result in results if self._is_query_relevant(query, result)]
            if results:
                return results
        return []

    @staticmethod
    def _is_query_relevant(query: str, result: SearchResult) -> bool:
        ignored = {
            "argentina", "australia", "brazil", "canada", "chile", "company",
            "contact", "france", "germany", "italy", "manufacturer", "mexico",
            "netherlands", "new", "norway", "spain", "sweden", "switzerland",
            "tender", "united", "usa", "zealand",
        }
        terms = {
            term.casefold()
            for term in re.findall(r"[A-Za-z][A-Za-z0-9-]{4,}", query)
            if term.casefold() not in ignored
        }
        if not terms:
            return True
        haystack = f"{getattr(result, 'title', '')} {getattr(result, 'snippet', '')} {getattr(result, 'url', '')}".casefold()
        return any(term in haystack for term in terms)


class DuckDuckGoHtmlSearch(SearchProvider):
    """Uses DuckDuckGo's public HTML endpoint without login or browser automation."""

    def __init__(self, user_agent: str, timeout: int = 15, pause_seconds: float = 1.0):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout = timeout
        self.pause_seconds = pause_seconds

    def search(self, query: str, limit: int) -> list[SearchResult]:
        response = self.session.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            timeout=self.timeout,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for result in soup.select(".result"):
            link = result.select_one(".result__a")
            if not link or not link.get("href"):
                continue
            url = self._clean_duck_url(link["href"])
            if self._is_search_or_directory_noise(url):
                continue
            snippet_tag = result.select_one(".result__snippet")
            results.append(
                SearchResult(
                    title=link.get_text(" ", strip=True),
                    url=url,
                    snippet=snippet_tag.get_text(" ", strip=True) if snippet_tag else "",
                    query=query,
                )
            )
            if len(results) >= limit:
                break
        time.sleep(self.pause_seconds)
        return results

    @staticmethod
    def _clean_duck_url(url: str) -> str:
        if "duckduckgo.com/l/" in url:
            parsed = urlparse(url)
            uddg = parse_qs(parsed.query).get("uddg", [""])[0]
            if uddg:
                return unquote(uddg)
        return url

    @staticmethod
    def _is_search_or_directory_noise(url: str) -> bool:
        domain = normalize_domain(url)
        noisy = {
            "linkedin.com",
            "indeed.com",
            "glassdoor.com",
            "youtube.com",
            "facebook.com",
            "x.com",
            "twitter.com",
            "wikipedia.org",
            "crunchbase.com",
        }
        return domain in noisy


class BingHtmlSearch(SearchProvider):
    """Uses Bing's public result page without login or API credentials."""

    def __init__(self, user_agent: str, timeout: int = 6, pause_seconds: float = 0.2):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout = timeout
        self.pause_seconds = pause_seconds

    def search(self, query: str, limit: int) -> list[SearchResult]:
        response = self.session.get(
            "https://www.bing.com/search",
            params={"q": query, "setlang": "en-US", "cc": "us"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for result in soup.select("li.b_algo"):
            link = result.select_one("h2 a[href]")
            if not link:
                continue
            url = self._clean_bing_url(link.get("href", ""))
            if not url or self._is_noise_url(url):
                continue
            snippet_tag = result.select_one(".b_caption p")
            results.append(
                SearchResult(
                    title=link.get_text(" ", strip=True),
                    url=url,
                    snippet=snippet_tag.get_text(" ", strip=True) if snippet_tag else "",
                    query=query,
                )
            )
            if len(results) >= limit:
                break
        if self.pause_seconds:
            time.sleep(self.pause_seconds)
        return results

    @staticmethod
    def _clean_bing_url(url: str) -> str:
        parsed = urlparse(url)
        if normalize_domain(url) != "bing.com" or not parsed.path.startswith("/ck/"):
            return url
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if not encoded.startswith("a1"):
            return url
        payload = encoded[2:]
        try:
            padding = "=" * (-len(payload) % 4)
            return base64.urlsafe_b64decode(payload + padding).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return url

    @staticmethod
    def _is_noise_url(url: str) -> bool:
        domain = normalize_domain(url)
        return domain in {
            "bing.com",
            "google.com",
            "google.fr",
            "linkedin.com",
            "facebook.com",
            "youtube.com",
            "wikipedia.org",
        }


class YahooHtmlSearch(SearchProvider):
    """Uses Yahoo's public search result page without login or API credentials."""

    def __init__(self, user_agent: str, timeout: int = 6, pause_seconds: float = 0.2):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout = timeout
        self.pause_seconds = pause_seconds

    def search(self, query: str, limit: int) -> list[SearchResult]:
        response = self.session.get(
            "https://search.yahoo.com/search",
            params={"p": query},
            timeout=self.timeout,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for result in soup.select("div.algo"):
            link = result.select_one(".compTitle a[href]")
            title = result.select_one("h3")
            if not link or not title:
                continue
            url = self._clean_yahoo_url(link.get("href", ""))
            if not url or BingHtmlSearch._is_noise_url(url):
                continue
            snippet_tag = result.select_one(".compText p")
            results.append(
                SearchResult(
                    title=title.get_text(" ", strip=True),
                    url=url,
                    snippet=snippet_tag.get_text(" ", strip=True) if snippet_tag else "",
                    query=query,
                )
            )
            if len(results) >= limit:
                break
        if self.pause_seconds:
            time.sleep(self.pause_seconds)
        return results

    @staticmethod
    def _clean_yahoo_url(url: str) -> str:
        if "/RU=" not in url:
            return url
        encoded = url.split("/RU=", 1)[1].split("/RK=", 1)[0]
        return unquote(encoded)


class GoogleHtmlSearch(SearchProvider):
    """Reads standard public Google result pages and never handles consent challenges."""

    def __init__(self, user_agent: str, timeout: int = 5, pause_seconds: float = 0.1):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout = timeout
        self.pause_seconds = pause_seconds

    def search(self, query: str, limit: int) -> list[SearchResult]:
        response = self.session.get(
            "https://www.google.com/search",
            params={"q": query, "num": min(limit, 10), "hl": "en"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for result in soup.select("div.MjjYud, div.g"):
            link = result.select_one("a[href] h3")
            if not link or not link.parent:
                continue
            url = self._clean_google_url(link.parent.get("href", ""))
            if not url or BingHtmlSearch._is_noise_url(url):
                continue
            snippet_tag = result.select_one("div.VwiC3b, div[data-sncf]")
            results.append(SearchResult(link.get_text(" ", strip=True), url, snippet_tag.get_text(" ", strip=True) if snippet_tag else "", query))
            if len(results) >= limit:
                break
        if self.pause_seconds:
            time.sleep(self.pause_seconds)
        return results

    @staticmethod
    def _clean_google_url(url: str) -> str:
        if url.startswith("/url?"):
            return parse_qs(urlparse(url).query).get("q", [""])[0]
        return url


class YandexHtmlSearch(SearchProvider):
    """Reads Yandex public result pages without login or challenge bypassing."""

    def __init__(self, user_agent: str, timeout: int = 5, pause_seconds: float = 0.1):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout = timeout
        self.pause_seconds = pause_seconds

    def search(self, query: str, limit: int) -> list[SearchResult]:
        response = self.session.get("https://yandex.com/search/", params={"text": query}, timeout=self.timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for result in soup.select("li.serp-item"):
            link = result.select_one("a.OrganicTitle-Link[href], a.Link[href]")
            if not link:
                continue
            url = link.get("href", "")
            if not url or BingHtmlSearch._is_noise_url(url):
                continue
            snippet_tag = result.select_one(".OrganicTextContentSpan, .TextContainer")
            results.append(SearchResult(link.get_text(" ", strip=True), url, snippet_tag.get_text(" ", strip=True) if snippet_tag else "", query))
            if len(results) >= limit:
                break
        if self.pause_seconds:
            time.sleep(self.pause_seconds)
        return results


class MojeekHtmlSearch(SearchProvider):
    """Reads Mojeek's public HTML results as a small independent search source."""

    def __init__(self, user_agent: str, timeout: int = 5, pause_seconds: float = 0.1):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.timeout = timeout
        self.pause_seconds = pause_seconds

    def search(self, query: str, limit: int) -> list[SearchResult]:
        response = self.session.get("https://www.mojeek.com/search", params={"q": query}, timeout=self.timeout)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for result in soup.select("ul.results-standard > li, li.result"):
            link = result.select_one("h2 a[href], a.title[href]")
            if not link:
                continue
            url = link.get("href", "")
            if not url or BingHtmlSearch._is_noise_url(url):
                continue
            snippet_tag = result.select_one("p.s, p")
            results.append(SearchResult(link.get_text(" ", strip=True), url, snippet_tag.get_text(" ", strip=True) if snippet_tag else "", query))
            if len(results) >= limit:
                break
        if self.pause_seconds:
            time.sleep(self.pause_seconds)
        return results


class BingWebSearchApi(SearchProvider):
    """Optional provider. Set BING_SEARCH_API_KEY to use Microsoft's official API."""

    def __init__(self, user_agent: str, timeout: int = 15):
        self.key = os.getenv("BING_SEARCH_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Ocp-Apim-Subscription-Key": self.key})
        self.timeout = timeout

    def search(self, query: str, limit: int) -> list[SearchResult]:
        if not self.key:
            return []
        response = self.session.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": min(limit, 50), "responseFilter": "Webpages"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return [
            SearchResult(
                title=item.get("name", ""),
                url=item.get("url", ""),
                snippet=item.get("snippet", ""),
                query=query,
            )
            for item in data.get("webPages", {}).get("value", [])
            if item.get("url")
        ]


def default_search_provider(user_agent: str, timeout: int) -> SearchProvider:
    providers: list[SearchProvider] = []
    if os.getenv("BING_SEARCH_API_KEY"):
        providers.append(BingWebSearchApi(user_agent=user_agent, timeout=timeout))
    public_timeout = min(timeout, 5)
    providers.extend(
        [
            DuckDuckGoHtmlSearch(user_agent=user_agent, timeout=public_timeout),
            YahooHtmlSearch(user_agent=user_agent, timeout=public_timeout),
            BingHtmlSearch(user_agent=user_agent, timeout=public_timeout),
            YandexHtmlSearch(user_agent=user_agent, timeout=public_timeout),
            MojeekHtmlSearch(user_agent=user_agent, timeout=public_timeout),
            GoogleHtmlSearch(user_agent=user_agent, timeout=public_timeout),
        ]
    )
    return FallbackSearchProvider(
        MultiSearchProvider(providers)
    )
