from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from scheduler_state import DEFAULT_STATE_PATH, SchedulerState


SCHEDULER_STATE_PATH = DEFAULT_STATE_PATH


def production_runner(dry_run: bool = False) -> int:
    command = [sys.executable, "main.py", "--mode", "daily_loop"]
    if dry_run:
        command.append("--dry-run")
    return subprocess.run(command, check=False).returncode


def run_if_due(
    now: datetime | None = None,
    runner: Callable[[], int] | None = None,
) -> int:
    current = now or datetime.now()
    state = SchedulerState.load(SCHEDULER_STATE_PATH, now=current)
    if not state.is_due(current):
        print(f"scheduler_gate: not_due next_run_at={state.next_run_at.isoformat(timespec='seconds')}", flush=True)
        return 0

    state.mark_started(current)
    state.save(SCHEDULER_STATE_PATH)
    exit_code = (runner or production_runner)()
    completed_at = now or datetime.now()
    if exit_code == 0:
        state.mark_completed(completed_at, "completed")
    else:
        state.mark_failed(completed_at, f"exit_code={exit_code}")
    state.save(SCHEDULER_STATE_PATH)
    print(
        f"scheduler_gate: {'completed' if exit_code == 0 else 'failed'} "
        f"next_run_at={state.next_run_at.isoformat(timespec='seconds')}",
        flush=True,
    )
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-due", action="store_true")
    args = parser.parse_args()
    now = datetime.now()
    if args.force_due:
        state = SchedulerState.load(SCHEDULER_STATE_PATH, now=now)
        state.next_run_at = now
        state.save(SCHEDULER_STATE_PATH)
    return run_if_due(now=now, runner=lambda: production_runner(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
