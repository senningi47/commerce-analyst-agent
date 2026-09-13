"""Unit tests for the Day 6 spool usage importer (scan + attempt assignment)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from commerce_agent.evaluation.spool_importer import (
    AttemptRow,
    SpoolUsageRecord,
    assign_attempts,
    scan_spool_dir,
)


def _turn_event(
    *,
    sequence: int,
    attempt_id: str = "11111111-1111-1111-1111-111111111111",
    task_id: str = "fake_account_7",
    mode: str = "a",
    experiment_id: str = "exp-1",
    recorded_at: str = "2026-09-14T08:00:00+00:00",
    prompt: int = 550,
    completion: int = 120,
    reasoning: int = 40,
    cache_hit: int = 10,
    cache_miss: int = 540,
    total: int = 670,
    cost: str = "0.000166",
) -> dict[str, object]:
    return {
        "run_scope_digest": "a" * 64,
        "attempt_id": attempt_id,
        "phase": "attempt",
        "sequence": sequence,
        "event_type": "model_turn",
        "task_id": task_id,
        "mode": mode,
        "experiment_id": experiment_id,
        "recorded_at": recorded_at,
        "payload": {
            "actual_model": "deepseek-flash",
            "finish_reason": "tool_calls",
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "reasoning_tokens": reasoning,
                "cache_hit_tokens": cache_hit,
                "cache_miss_tokens": cache_miss,
                "total_tokens": total,
            },
            "cost": {
                "amount": cost,
                "currency": "USD",
                "price_band": "off_peak",
                "price_snapshot_id": "deepseek-flash-usd-2026-09-12",
            },
        },
    }


def _write_events(path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(e, sort_keys=True) + "\n" for e in events)
    path.write_text(lines, encoding="utf-8")


_FIXED_ATTEMPT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _record(
    *,
    attempt_id: UUID = _FIXED_ATTEMPT_ID,
    task_id: str = "fake_account_7",
    mode: str = "a",
    first: datetime | None = datetime(2026, 9, 14, 8, 0, 0, tzinfo=UTC),
    last: datetime | None = datetime(2026, 9, 14, 8, 0, 5, tzinfo=UTC),
    cost: str = "0.001",
) -> SpoolUsageRecord:
    return SpoolUsageRecord(
        attempt_id=attempt_id,
        experiment_id="exp-1",
        task_id=task_id,
        mode=mode,
        turns=1,
        prompt_tokens=1,
        completion_tokens=1,
        reasoning_tokens=0,
        cache_hit_tokens=0,
        cache_miss_tokens=1,
        total_tokens=2,
        cost_usd=Decimal(cost),
        price_snapshot_id="deepseek-flash-usd-2026-09-12",
        price_band="off_peak",
        first_recorded_at=first,
        last_recorded_at=last,
    )


def _attempt(
    *,
    attempt_id: UUID,
    task_id: str = "fake_account_7",
    mode: str = "a",
    experiment_id: str = "exp-1",
    started_at: datetime,
    finished_at: datetime | None,
) -> AttemptRow:
    return AttemptRow(
        attempt_id=attempt_id,
        experiment_id=experiment_id,
        task_id=task_id,
        mode=mode,
        started_at=started_at,
        finished_at=finished_at,
    )


class TestScanSpoolDir:
    def test_aggregates_usage_per_task_mode(self, tmp_path) -> None:
        _write_events(
            tmp_path / "11111111-1111-1111-1111-111111111111.jsonl",
            [
                _turn_event(sequence=0, recorded_at="2026-09-14T08:00:00+00:00"),
                _turn_event(
                    sequence=1,
                    recorded_at="2026-09-14T08:00:05+00:00",
                    prompt=600,
                    completion=140,
                    reasoning=50,
                    cache_hit=20,
                    cache_miss=580,
                    total=740,
                    cost="0.000200",
                ),
            ],
        )
        _write_events(
            tmp_path / "22222222-2222-2222-2222-222222222222.jsonl",
            [_turn_event(sequence=0, task_id="households_4", mode="c", cost="0.000300")],
        )

        report = scan_spool_dir(tmp_path)

        assert report.files_scanned == 2
        assert report.files_skipped == 0
        assert len(report.records) == 2
        by_task = {r.task_id: r for r in report.records}
        a_record = by_task["fake_account_7"]
        assert a_record.mode == "a"
        assert a_record.experiment_id == "exp-1"
        assert a_record.turns == 2
        assert a_record.prompt_tokens == 1150
        assert a_record.completion_tokens == 260
        assert a_record.reasoning_tokens == 90
        assert a_record.cache_hit_tokens == 30
        assert a_record.cache_miss_tokens == 1120
        assert a_record.total_tokens == 1410
        assert a_record.cost_usd == Decimal("0.000366")
        assert a_record.first_recorded_at == datetime(2026, 9, 14, 8, 0, 0, tzinfo=UTC)
        assert a_record.last_recorded_at == datetime(2026, 9, 14, 8, 0, 5, tzinfo=UTC)
        c_record = by_task["households_4"]
        assert c_record.turns == 1
        assert c_record.cost_usd == Decimal("0.000300")

    def test_one_bad_line_discards_whole_file(self, tmp_path) -> None:
        good_file = tmp_path / "11111111-1111-1111-1111-111111111111.jsonl"
        good_file.parent.mkdir(parents=True, exist_ok=True)
        good_file.write_text(
            json.dumps(_turn_event(sequence=0), sort_keys=True)
            + "\n"
            + "not-json\n"
            + json.dumps(_turn_event(sequence=1), sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "broken.jsonl").write_bytes(b'{"run_scope_digest": "a')
        forbidden = _turn_event(sequence=2)
        forbidden["payload"]["sol_sql"] = "SELECT 1"  # evaluator-only key must never import
        _write_events(
            tmp_path / "33333333-3333-3333-3333-333333333333.jsonl",
            [forbidden],
        )
        _write_events(
            tmp_path / "22222222-2222-2222-2222-222222222222.jsonl",
            [_turn_event(sequence=0, attempt_id="22222222-2222-2222-2222-222222222222")],
        )

        report = scan_spool_dir(tmp_path)

        assert report.files_scanned == 4
        assert report.files_skipped == 3  # bad-line file + broken file + forbidden-key file
        assert len(report.records) == 1
        assert report.records[0].task_id == "fake_account_7"
        assert report.records[0].turns == 1

    def test_skips_file_with_sequence_regression(self, tmp_path) -> None:
        _write_events(
            tmp_path / "44444444-4444-4444-4444-444444444444.jsonl",
            [
                _turn_event(sequence=3),
                _turn_event(sequence=1),
            ],
        )

        report = scan_spool_dir(tmp_path)

        assert report.files_skipped == 1
        assert report.records == ()

    def test_skips_legacy_files_without_task_id(self, tmp_path) -> None:
        legacy = _turn_event(sequence=0)
        for key in ("task_id", "mode", "experiment_id", "recorded_at"):
            legacy.pop(key)
        _write_events(
            tmp_path / "55555555-5555-5555-5555-555555555555.jsonl",
            [legacy],
        )
        _write_events(
            tmp_path / "11111111-1111-1111-1111-111111111111.jsonl",
            [_turn_event(sequence=0)],
        )

        report = scan_spool_dir(tmp_path)

        assert report.files_skipped == 1
        assert len(report.records) == 1
        assert report.records[0].task_id == "fake_account_7"

    def test_file_with_missing_cost_payload_is_discarded(self, tmp_path) -> None:
        partial = _turn_event(sequence=0)
        del partial["payload"]["cost"]
        _write_events(
            tmp_path / "11111111-1111-1111-1111-111111111111.jsonl",
            [partial, _turn_event(sequence=1)],
        )

        report = scan_spool_dir(tmp_path)

        assert report.files_skipped == 1
        assert report.records == ()


class TestAssignAttempts:
    def test_single_match_assigns_directly(self) -> None:
        agent_record = _record()
        eval_attempt = uuid4()
        assignment = assign_attempts(
            [agent_record],
            [
                _attempt(
                    attempt_id=eval_attempt,
                    started_at=datetime(2026, 9, 14, 7, 59, 0, tzinfo=UTC),
                    finished_at=datetime(2026, 9, 14, 8, 1, 0, tzinfo=UTC),
                )
            ],
        )
        assert assignment.assigned == {eval_attempt: agent_record}
        assert assignment.unassigned == ()
        assert assignment.ambiguous == ()

    def test_time_window_disambiguates_retry_attempts(self) -> None:
        first_session = _record(attempt_id=_FIXED_ATTEMPT_ID, cost="0.001")
        retry_session = _record(
            attempt_id=UUID("22222222-2222-2222-2222-222222222222"),
            first=datetime(2026, 9, 14, 9, 0, 0, tzinfo=UTC),
            last=datetime(2026, 9, 14, 9, 0, 5, tzinfo=UTC),
            cost="0.002",
        )
        first_attempt = uuid4()
        retry_attempt = uuid4()
        assignment = assign_attempts(
            [first_session, retry_session],
            [
                _attempt(
                    attempt_id=first_attempt,
                    started_at=datetime(2026, 9, 14, 7, 59, 0, tzinfo=UTC),
                    finished_at=datetime(2026, 9, 14, 8, 1, 0, tzinfo=UTC),
                ),
                _attempt(
                    attempt_id=retry_attempt,
                    started_at=datetime(2026, 9, 14, 8, 59, 0, tzinfo=UTC),
                    finished_at=datetime(2026, 9, 14, 9, 1, 0, tzinfo=UTC),
                ),
            ],
            clock_grace=timedelta(seconds=60),
        )
        assert assignment.assigned == {first_attempt: first_session, retry_attempt: retry_session}

    def test_no_matching_attempt_goes_unassigned(self) -> None:
        agent_record = _record(task_id="ghost_task")
        assignment = assign_attempts([agent_record], [])
        assert assignment.assigned == {}
        assert len(assignment.unassigned) == 1
        assert assignment.ambiguous == ()

    def test_overlapping_windows_without_disambiguation_is_ambiguous(self) -> None:
        agent_record = _record()
        first_attempt = uuid4()
        second_attempt = uuid4()
        started = datetime(2026, 9, 14, 7, 59, 0, tzinfo=UTC)
        finished = datetime(2026, 9, 14, 8, 1, 0, tzinfo=UTC)
        assignment = assign_attempts(
            [agent_record],
            [
                _attempt(attempt_id=first_attempt, started_at=started, finished_at=finished),
                _attempt(attempt_id=second_attempt, started_at=started, finished_at=finished),
            ],
        )
        assert assignment.assigned == {}
        assert assignment.unassigned == ()
        assert assignment.ambiguous == (agent_record,)

    def test_agent_record_without_timestamps_needs_unique_group(self) -> None:
        agent_record = _record(first=None, last=None)
        only_attempt = uuid4()
        assignment = assign_attempts(
            [agent_record],
            [
                _attempt(
                    attempt_id=only_attempt,
                    started_at=datetime(2026, 9, 14, 8, 0, 0, tzinfo=UTC),
                    finished_at=None,
                )
            ],
        )
        assert assignment.assigned == {only_attempt: agent_record}
