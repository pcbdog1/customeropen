from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunState:
    path: Path
    seen_urls: set[str] = field(default_factory=set)
    completed_queries: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: str | Path) -> "RunState":
        state_path = Path(path)
        if not state_path.exists():
            return cls(path=state_path)
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return cls(
            path=state_path,
            seen_urls=set(data.get("seen_urls", [])),
            completed_queries=set(data.get("completed_queries", [])),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "seen_urls": sorted(self.seen_urls),
                    "completed_queries": sorted(self.completed_queries),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def has_seen_url(self, url: str) -> bool:
        return url.rstrip("/") in self.seen_urls

    def mark_url_seen(self, url: str) -> None:
        self.seen_urls.add(url.rstrip("/"))

    def mark_query_completed(self, query: str) -> None:
        self.completed_queries.add(query)

    def has_completed_query(self, query: str) -> bool:
        return query in self.completed_queries
