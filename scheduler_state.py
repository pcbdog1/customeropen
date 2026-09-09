from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


SCHEDULE_INTERVAL_SECONDS = 19_800
DEFAULT_STATE_PATH = Path("state/scheduler_state.json")


@dataclass
class SchedulerState:
    next_run_at: datetime
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_result: str = ""

    @classmethod
    def load(cls, path: Path = DEFAULT_STATE_PATH, now: datetime | None = None) -> "SchedulerState":
        if not path.exists():
            return cls(next_run_at=now or datetime.now())
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            next_run_at=datetime.fromisoformat(data["next_run_at"]),
            last_started_at=cls._parse_datetime(data.get("last_started_at")),
            last_completed_at=cls._parse_datetime(data.get("last_completed_at")),
            last_result=str(data.get("last_result") or ""),
        )

    @staticmethod
    def _parse_datetime(value: object) -> datetime | None:
        return datetime.fromisoformat(str(value)) if value else None

    def is_due(self, now: datetime | None = None) -> bool:
        return (now or datetime.now()) >= self.next_run_at

    def mark_started(self, now: datetime | None = None) -> None:
        self.last_started_at = now or datetime.now()
        self.last_result = "running"

    def mark_completed(self, now: datetime | None = None, result: str = "completed") -> None:
        completed_at = now or datetime.now()
        while self.next_run_at <= completed_at:
            self.next_run_at += timedelta(seconds=SCHEDULE_INTERVAL_SECONDS)
        self.last_completed_at = completed_at
        self.last_result = result

    def mark_failed(self, now: datetime | None = None, result: str = "failed") -> None:
        failed_at = now or datetime.now()
        self.last_completed_at = failed_at
        self.next_run_at = failed_at + timedelta(seconds=SCHEDULE_INTERVAL_SECONDS)
        self.last_result = result

    def save(self, path: Path = DEFAULT_STATE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "next_run_at": self.next_run_at.isoformat(timespec="seconds"),
                    "last_started_at": self.last_started_at.isoformat(timespec="seconds") if self.last_started_at else "",
                    "last_completed_at": self.last_completed_at.isoformat(timespec="seconds") if self.last_completed_at else "",
                    "last_result": self.last_result,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
