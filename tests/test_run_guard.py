from __future__ import annotations

from datetime import datetime, timedelta

import run_guard


def test_fresh_lock_blocks_second_run(tmp_path, monkeypatch):
    lock_path = tmp_path / "daily_loop.lock"
    monkeypatch.setattr(run_guard, "LOCK_PATH", lock_path)

    first = run_guard.RunLock.acquire(lock_path)
    try:
        try:
            run_guard.RunLock.acquire(lock_path)
        except RuntimeError as exc:
            assert "already running" in str(exc)
        else:
            raise AssertionError("fresh lock did not block second run")
    finally:
        first.release()


def test_stale_lock_is_recovered(tmp_path, monkeypatch):
    lock_path = tmp_path / "daily_loop.lock"
    checkpoint_path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(run_guard, "LOCK_PATH", lock_path)
    monkeypatch.setattr(run_guard, "CHECKPOINT_PATH", checkpoint_path)
    old = datetime.now() - timedelta(hours=7)
    lock_path.write_text(
        '{"pid": 123, "started_at": "%s", "updated_at": "%s", "stage": "send_new"}' % (old.isoformat(timespec="seconds"), old.isoformat(timespec="seconds")),
        encoding="utf-8",
    )

    recovered = run_guard.RunLock.acquire(lock_path)
    try:
        assert recovered.recovered_from_stale_lock is True
        checkpoint = run_guard.read_checkpoint()
        assert "stale" in checkpoint["interruption_reason"]
    finally:
        recovered.release()


def test_dead_process_lock_is_recovered_before_six_hours(tmp_path, monkeypatch):
    lock_path = tmp_path / "daily_loop.lock"
    checkpoint_path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(run_guard, "LOCK_PATH", lock_path)
    monkeypatch.setattr(run_guard, "CHECKPOINT_PATH", checkpoint_path)
    lock_path.write_text(
        '{"pid": 123, "started_at": "%s", "updated_at": "%s", "stage": "send_new"}'
        % (datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")),
        encoding="utf-8",
    )

    def dead_process(_pid, _signal):
        raise ProcessLookupError

    monkeypatch.setattr(run_guard.os, "kill", dead_process)
    recovered = run_guard.RunLock.acquire(lock_path)
    try:
        assert recovered.recovered_from_stale_lock is True
        checkpoint = run_guard.read_checkpoint()
        assert "process" in checkpoint["interruption_reason"]
    finally:
        recovered.release()


def test_checkpoint_and_summary_are_written(tmp_path, monkeypatch):
    checkpoint_path = tmp_path / "checkpoint.json"
    summary_path = tmp_path / "summary.json"
    monkeypatch.setattr(run_guard, "CHECKPOINT_PATH", checkpoint_path)
    monkeypatch.setattr(run_guard, "RUN_SUMMARY_PATH", summary_path)

    run_guard.write_checkpoint(
        stage="generate_drafts_complete",
        completed_leads=7,
        generated_emails=5,
        sent_emails=2,
        last_customer="ACME",
        last_email="info@example.com",
        interruption_reason="",
    )
    run_guard.write_run_summary(
        completed=True,
        recovered_from_interruption=False,
        sent_count=2,
        new_lead_count=7,
        failure_reason="",
    )

    checkpoint = run_guard.read_checkpoint()
    summary = run_guard.read_run_summary()
    assert checkpoint["stage"] == "generate_drafts_complete"
    assert checkpoint["completed_leads"] == 7
    assert checkpoint["generated_emails"] == 5
    assert checkpoint["sent_emails"] == 2
    assert checkpoint["last_customer"] == "ACME"
    assert summary["completed"] is True
    assert summary["sent_count"] == 2


def test_checkpoint_and_summary_support_isolated_dry_run_paths(tmp_path):
    checkpoint_path = tmp_path / "checkpoint.dry_run.json"
    summary_path = tmp_path / "summary.dry_run.json"

    run_guard.write_checkpoint(stage="dry_run", path=checkpoint_path)
    run_guard.write_run_summary(
        completed=True,
        recovered_from_interruption=False,
        sent_count=0,
        new_lead_count=3,
        path=summary_path,
    )

    assert run_guard.read_checkpoint(checkpoint_path)["stage"] == "dry_run"
    assert run_guard.read_run_summary(summary_path)["new_lead_count"] == 3
