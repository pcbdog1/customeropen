from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


LOCK_PATH = Path("state/daily_loop.lock")
CHECKPOINT_PATH = Path("state/daily_loop_checkpoint.json")
RUN_SUMMARY_PATH = Path("state/run_summary.json")
STALE_LOCK_SECONDS = 6 * 60 * 60


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_checkpoint(path: Path | None = None) -> dict[str, Any]:
    return _read_json(path or CHECKPOINT_PATH)


def write_checkpoint(
    *,
    stage: str,
    completed_leads: int = 0,
    generated_emails: int = 0,
    sent_emails: int = 0,
    last_customer: str = "",
    last_email: str = "",
    interruption_reason: str = "",
    path: Path | None = None,
) -> None:
    target = path or CHECKPOINT_PATH
    previous = read_checkpoint(target)
    _write_json(
        target,
        {
            "stage": stage,
            "completed_leads": completed_leads,
            "generated_emails": generated_emails,
            "sent_emails": sent_emails,
            "last_customer": last_customer or previous.get("last_customer", ""),
            "last_email": last_email or previous.get("last_email", ""),
            "interruption_reason": interruption_reason,
            "updated_at": _now(),
        },
    )


def read_run_summary(path: Path | None = None) -> dict[str, Any]:
    return _read_json(path or RUN_SUMMARY_PATH)


def write_run_summary(
    *,
    completed: bool,
    recovered_from_interruption: bool,
    sent_count: int,
    new_lead_count: int,
    failure_reason: str = "",
    path: Path | None = None,
) -> None:
    _write_json(
        path or RUN_SUMMARY_PATH,
        {
            "completed": completed,
            "recovered_from_interruption": recovered_from_interruption,
            "sent_count": sent_count,
            "new_lead_count": new_lead_count,
            "failure_reason": failure_reason,
            "finished_at": _now(),
        },
    )


def lock_status(path: Path | None = None) -> dict[str, Any]:
    path = path or LOCK_PATH
    data = _read_json(path)
    exists = path.exists()
    owner_alive = True
    pid = data.get("pid")
    if exists and isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            owner_alive = False
        except PermissionError:
            owner_alive = True
        except OSError:
            owner_alive = False
    updated_at = str(data.get("updated_at") or "")
    age_seconds = None
    stale = False
    if exists:
        try:
            age_seconds = (datetime.now() - datetime.fromisoformat(updated_at)).total_seconds()
        except ValueError:
            age_seconds = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds()
        stale = bool(age_seconds is not None and age_seconds > STALE_LOCK_SECONDS)
    return {
        "exists": exists,
        "stale": stale,
        "owner_alive": owner_alive,
        "age_seconds": age_seconds,
        "path": str(path),
        "data": data,
    }


@dataclass
class RunLock:
    path: Path = LOCK_PATH
    recovered_from_stale_lock: bool = False

    @classmethod
    def acquire(cls, path: Path | None = None, checkpoint_path: Path | None = None) -> "RunLock":
        path = path or LOCK_PATH
        checkpoint_path = checkpoint_path or CHECKPOINT_PATH
        status = lock_status(path)
        recovered = False
        if status["exists"]:
            if not status["stale"] and status["owner_alive"]:
                pid = status["data"].get("pid", "unknown")
                stage = status["data"].get("stage", "unknown")
                raise RuntimeError(f"daily_loop is already running or locked by pid {pid}, stage {stage}")
            recovered = True
            path.unlink(missing_ok=True)
            checkpoint = read_checkpoint(checkpoint_path)
            write_checkpoint(
                stage=str(checkpoint.get("stage") or "recovered_stale_lock"),
                completed_leads=int(checkpoint.get("completed_leads") or 0),
                generated_emails=int(checkpoint.get("generated_emails") or 0),
                sent_emails=int(checkpoint.get("sent_emails") or 0),
                last_customer=str(checkpoint.get("last_customer") or ""),
                last_email=str(checkpoint.get("last_email") or ""),
                interruption_reason=(
                    "Previous lock was stale for more than 6 hours."
                    if status["stale"]
                    else "Previous lock owner process no longer exists."
                ), path=checkpoint_path,
            )
        lock = cls(path=path, recovered_from_stale_lock=recovered)
        lock.heartbeat("start")
        return lock

    def heartbeat(self, stage: str = "") -> None:
        existing = _read_json(self.path)
        started_at = existing.get("started_at") or _now()
        _write_json(
            self.path,
            {
                "pid": os.getpid(),
                "started_at": started_at,
                "updated_at": _now(),
                "stage": stage or existing.get("stage", ""),
            },
        )

    def release(self) -> None:
        self.path.unlink(missing_ok=True)


def estimate_next_launchd_run(last_run_date: str, interval_seconds: int = 19800) -> str:
    if not last_run_date:
        return "Unknown"
    try:
        last = datetime.fromisoformat(last_run_date)
    except ValueError:
        return "Unknown"
    return (last + timedelta(seconds=interval_seconds)).isoformat(timespec="seconds")
