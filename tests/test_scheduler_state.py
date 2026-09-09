from __future__ import annotations

from datetime import datetime, timedelta

from scheduler_state import SchedulerState


def test_scheduler_state_does_not_run_before_persisted_due_time(tmp_path):
    path = tmp_path / "scheduler_state.json"
    due = datetime(2026, 7, 10, 14, 30, 0)
    SchedulerState(next_run_at=due).save(path)

    state = SchedulerState.load(path)

    assert state.is_due(due - timedelta(seconds=1)) is False
    assert state.is_due(due) is True


def test_completed_run_advances_from_previous_due_time_not_completion_time(tmp_path):
    path = tmp_path / "scheduler_state.json"
    due = datetime(2026, 7, 10, 14, 30, 0)
    SchedulerState(next_run_at=due).save(path)

    state = SchedulerState.load(path)
    state.mark_completed(due + timedelta(minutes=17), "completed")

    assert state.next_run_at == due + timedelta(seconds=19800)
    assert state.last_result == "completed"


def test_failed_run_schedules_next_retry_from_failure_time(tmp_path):
    path = tmp_path / "scheduler_state.json"
    due = datetime(2026, 7, 10, 14, 30, 0)
    state = SchedulerState(next_run_at=due)

    state.mark_failed(due + timedelta(minutes=1), "exit_code=1")

    assert state.next_run_at == due + timedelta(minutes=1, seconds=19800)
    assert state.last_result == "exit_code=1"


def test_gate_skips_not_due_run_and_does_not_change_schedule(tmp_path, monkeypatch):
    import scheduler_gate

    path = tmp_path / "scheduler_state.json"
    due = datetime(2026, 7, 10, 14, 30, 0)
    SchedulerState(next_run_at=due).save(path)
    monkeypatch.setattr(scheduler_gate, "SCHEDULER_STATE_PATH", path)
    calls: list[bool] = []

    result = scheduler_gate.run_if_due(
        now=due - timedelta(seconds=1),
        runner=lambda: calls.append(True) or 0,
    )

    assert result == 0
    assert calls == []
    assert SchedulerState.load(path).next_run_at == due


def test_gate_marks_completion_only_after_successful_runner(tmp_path, monkeypatch):
    import scheduler_gate

    path = tmp_path / "scheduler_state.json"
    due = datetime(2026, 7, 10, 14, 30, 0)
    SchedulerState(next_run_at=due).save(path)
    monkeypatch.setattr(scheduler_gate, "SCHEDULER_STATE_PATH", path)

    result = scheduler_gate.run_if_due(now=due, runner=lambda: 0)

    assert result == 0
    state = SchedulerState.load(path)
    assert state.next_run_at == due + timedelta(seconds=19800)
    assert state.last_result == "completed"
