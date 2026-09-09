from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


KEYWORD_PERFORMANCE_PATH = Path("state/keyword_performance.json")


class KeywordPerformanceStore:
    def __init__(self, path: Path = KEYWORD_PERFORMANCE_PATH) -> None:
        self.path = path

    @staticmethod
    def _key(query: str, source_type: str) -> str:
        return f"{source_type.strip().casefold()}::{query.strip().casefold()}"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "queries": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "queries": {}}
        data.setdefault("version", 1)
        data.setdefault("queries", {})
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _defaults(query: str, source_type: str) -> dict[str, object]:
        return {
            "query": query,
            "source_type": source_type,
            "search_count": 0,
            "candidate_count": 0,
            "email_count": 0,
            "new_lead_count": 0,
            "new_domain_count": 0,
            "qualified_hardware_count": 0,
            "successful_send_count": 0,
            "consecutive_zero_email_count": 0,
            "paused": False,
            "last_used_at": "",
        }

    @classmethod
    def _with_defaults(cls, record: dict, query: str, source_type: str) -> dict:
        for field, value in cls._defaults(query, source_type).items():
            record.setdefault(field, value)
        return record

    def record(
        self,
        query: str,
        source_type: str,
        candidates: int,
        emails: int,
        new_leads: int,
        sent: int = 0,
        *,
        new_domains: int | None = None,
        qualified_hardware: int | None = None,
    ) -> None:
        data = self._load()
        key = self._key(query, source_type)
        record = self._with_defaults(
            data["queries"].setdefault(key, {}),
            query,
            source_type,
        )
        record["query"] = query
        record["source_type"] = source_type
        record["search_count"] += 1
        record["candidate_count"] += max(0, int(candidates))
        record["email_count"] += max(0, int(emails))
        record["new_lead_count"] += max(0, int(new_leads))
        record["new_domain_count"] += max(0, int(0 if new_domains is None else new_domains))
        record["qualified_hardware_count"] += max(
            0,
            int(0 if qualified_hardware is None else qualified_hardware),
        )
        record["successful_send_count"] += max(0, int(sent))
        if emails > 0:
            record["consecutive_zero_email_count"] = 0
        else:
            record["consecutive_zero_email_count"] += 1
        record["paused"] = record["consecutive_zero_email_count"] >= 3
        record["last_used_at"] = datetime.now().isoformat(timespec="seconds")
        self._save(data)

    def ordered(self, queries: list[tuple[str, str]]) -> list[tuple[str, str]]:
        records = self._load()["queries"]
        ranked: list[tuple[tuple, tuple[str, str]]] = []
        for position, item in enumerate(queries):
            query, source_type = item
            record = records.get(self._key(query, source_type))
            if not record:
                rank = (0, 1, 0.0, 0.0, 0.0, position)
            else:
                searches = max(1, int(record.get("search_count", 0)))
                emails = int(record.get("email_count", 0))
                leads = int(record.get("new_lead_count", 0))
                domains = int(record.get("new_domain_count", 0))
                qualified = int(record.get("qualified_hardware_count", 0))
                productive = emails > 0 or qualified > 0 or int(record.get("successful_send_count", 0)) > 0
                group = 0 if productive else 2
                paused = 1 if record.get("paused") else 0
                rank = (paused, group, -(emails / searches), -(qualified / searches), -(domains / searches), position)
            ranked.append((rank, item))
        ranked.sort(key=lambda value: value[0])
        return [item for _, item in ranked]

    def record_sent(self, query: str, source_type: str, count: int = 1) -> None:
        data = self._load()
        key = self._key(query, source_type)
        normalized_query = query.strip().casefold()
        for existing_key, existing_record in data["queries"].items():
            if str(existing_record.get("query", "")).strip().casefold() == normalized_query:
                key = existing_key
                break
        record = self._with_defaults(
            data["queries"].setdefault(key, {}),
            query,
            source_type,
        )
        record["successful_send_count"] += max(0, int(count))
        record["last_used_at"] = datetime.now().isoformat(timespec="seconds")
        self._save(data)

    def ranked_queries(self, *, include_paused: bool = True) -> list[dict[str, object]]:
        records = list(self._load()["queries"].values())
        if not include_paused:
            records = [record for record in records if not record.get("paused", False)]
        records.sort(
            key=lambda record: (
                -int(record.get("successful_send_count", 0)),
                -(int(record.get("qualified_hardware_count", 0)) / max(1, int(record.get("search_count", 0)))),
                -(int(record.get("email_count", 0)) / max(1, int(record.get("search_count", 0)))),
                -int(record.get("new_domain_count", 0)),
                -int(record.get("new_lead_count", 0)),
                str(record.get("query", "")).casefold(),
                str(record.get("source_type", "")).casefold(),
            )
        )
        return records

    def top_queries(self, limit: int = 5) -> list[dict[str, object]]:
        return self.ranked_queries()[: max(0, int(limit))]
