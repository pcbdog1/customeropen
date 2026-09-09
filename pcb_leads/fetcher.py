from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests


@dataclass
class FetchedPage:
    url: str
    html: str


class PublicPageFetcher:
    def __init__(self, user_agent: str, timeout: int = 15, pause_seconds: float = 1.0):
        self.user_agent = user_agent
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    def can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots_cache:
            parser = urllib.robotparser.RobotFileParser()
            try:
                robots_url = urljoin(root, "/robots.txt")
                response = self.session.get(robots_url, timeout=self.timeout)
                if response.status_code < 400:
                    parser.parse(response.text.splitlines())
                else:
                    parser.parse([])
            except Exception:
                return True
            self._robots_cache[root] = parser
        return self._robots_cache[root].can_fetch(self.user_agent, url)

    def fetch(self, url: str) -> FetchedPage | None:
        if not self.can_fetch(url):
            return None
        try:
            response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            content_type = response.headers.get("Content-Type", "")
            if response.status_code >= 400 or "text/html" not in content_type:
                return None
            time.sleep(self.pause_seconds)
            return FetchedPage(url=response.url, html=response.text)
        except requests.RequestException:
            return None
