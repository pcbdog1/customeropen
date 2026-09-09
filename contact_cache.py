from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


CONTACT_CACHE_PATH = Path("state/contact_cache.json")
NO_RESULT_COOLDOWN_DAYS = 14


@dataclass(frozen=True)
class ContactCacheEntry:
    outcome: str
    source_type: str
    source_url: str
    email: str
    email_type: str
    updated_at: datetime

    @property
    def is_in_cooldown(self) -> bool:
        return self.outcome != "found" and datetime.now() < self.updated_at + timedelta(days=NO_RESULT_COOLDOWN_DAYS)


class ContactCache:
    def __init__(self, path: Path = CONTACT_CACHE_PATH) -> None:
        self.path = path

    def _load(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _normalize_source_type(source_type: str) -> str:
        return re.sub(r"\s+", " ", str(source_type or "").strip()).casefold()

    @staticmethod
    def _normalize_source_url(source_url: str) -> str:
        value = str(source_url or "").strip()
        if not value:
            return ""
        parsed = urlsplit(value)
        if not parsed.scheme and not parsed.netloc:
            return value.rstrip("/")
        return urlunsplit(
            (
                parsed.scheme.casefold(),
                parsed.netloc.casefold(),
                parsed.path.rstrip("/"),
                parsed.query,
                "",
            )
        )

    @classmethod
    def _source_key(cls, source_type: str, source_url: str) -> str:
        return json.dumps(
            [cls._normalize_source_type(source_type), cls._normalize_source_url(source_url)],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _entry(data: object) -> ContactCacheEntry | None:
        if not isinstance(data, dict):
            return None
        try:
            updated_at = datetime.fromisoformat(str(data["updated_at"]))
        except (KeyError, TypeError, ValueError):
            return None
        return ContactCacheEntry(
            outcome=str(data.get("outcome") or "not_found"),
            source_type=str(data.get("source_type") or ""),
            source_url=str(data.get("source_url") or ""),
            email=str(data.get("email") or ""),
            email_type=str(data.get("email_type") or ""),
            updated_at=updated_at,
        )

    @classmethod
    def _found_entry(cls, data: object) -> ContactCacheEntry | None:
        if not isinstance(data, dict):
            return None
        if "found" in data:
            return cls._entry(data["found"])
        entry = cls._entry(data)
        return entry if entry and entry.outcome == "found" else None

    @classmethod
    def _not_found_entries(cls, data: object) -> dict[str, ContactCacheEntry]:
        if not isinstance(data, dict):
            return {}
        if "not_found" in data and isinstance(data["not_found"], dict):
            return {
                key: entry
                for key, value in data["not_found"].items()
                if (entry := cls._entry(value)) and entry.outcome != "found"
            }
        entry = cls._entry(data)
        if entry and entry.outcome != "found":
            return {cls._source_key(entry.source_type, entry.source_url): entry}
        return {}

    @classmethod
    def _payload(
        cls,
        outcome: str,
        source_type: str,
        source_url: str,
        email: str,
        email_type: str,
        updated_at: datetime,
    ) -> dict[str, str]:
        return {
            "outcome": outcome,
            "source_type": cls._normalize_source_type(source_type),
            "source_url": cls._normalize_source_url(source_url),
            "email": email,
            "email_type": email_type,
            "updated_at": updated_at.isoformat(timespec="seconds"),
        }

    @classmethod
    def _entry_payload(cls, entry: ContactCacheEntry) -> dict[str, str]:
        return cls._payload(
            entry.outcome,
            entry.source_type,
            entry.source_url,
            entry.email,
            entry.email_type,
            entry.updated_at,
        )

    def lookup(
        self,
        domain: str,
        source_type: str = "",
        source_url: str = "",
        allow_not_found_retry: bool = False,
        now: datetime | None = None,
    ) -> ContactCacheEntry | None:
        data = self._load().get(domain.lower())
        found = self._found_entry(data)
        if found:
            return found
        if allow_not_found_retry:
            return None
        entry = self._not_found_entries(data).get(self._source_key(source_type, source_url))
        if not entry or (now or datetime.now()) >= entry.updated_at + timedelta(days=NO_RESULT_COOLDOWN_DAYS):
            return None
        return entry

    def record(
        self,
        domain: str,
        outcome: str,
        source_url: str,
        email: str,
        email_type: str,
        source_type: str = "",
        now: datetime | None = None,
    ) -> None:
        data = self._load()
        key = domain.lower()
        current = data.get(key)
        found = self._found_entry(current)
        not_found = self._not_found_entries(current)
        timestamp = now or datetime.now()
        payload = self._payload(outcome, source_type, source_url, email, email_type, timestamp)
        if outcome == "found":
            found = self._entry(payload)
        else:
            not_found[self._source_key(source_type, source_url)] = self._entry(payload)

        value: dict[str, object] = {
            "not_found": {source_key: self._entry_payload(entry) for source_key, entry in not_found.items()},
        }
        if found:
            value["found"] = self._entry_payload(found)
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
