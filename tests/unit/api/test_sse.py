"""SSE generator behavior: replay cursor, heartbeats, and block format.

starlette's TestClient buffers the whole response body, so an infinite SSE
stream cannot be consumed through it; the wire behavior is therefore tested
by driving the async generator directly, which is exactly what the route
wraps in a StreamingResponse.
"""

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from commerce_agent.api.events import SseRunEvent
from commerce_agent.api.sse import parse_last_event_id, sse_stream

RUN_ID = uuid4()


class FakeSource:
    """Scripted pages of events; logs every cursor query."""

    def __init__(self, pages: list[tuple[SseRunEvent, ...]]) -> None:
        self._pages = list(pages)
        self.calls: list[int] = []

    async def events_after(
        self, run_id: UUID, *, after_sequence: int, limit: int = 200
    ) -> tuple[SseRunEvent, ...]:
        del run_id, limit
        self.calls.append(after_sequence)
        return self._pages.pop(0) if self._pages else ()


def _event(sequence: int) -> SseRunEvent:
    return SseRunEvent(
        cursor=sequence + 1,
        run_id=RUN_ID,
        attempt_id=uuid4(),
        sequence=sequence,
        event_type="proposal_created",
        status="succeeded",
        occurred_at=datetime(2026, 9, 14, 9, 0, tzinfo=UTC),
    )


async def _take(stream: object, count: int) -> list[str]:
    chunks: list[str] = []
    async for chunk in stream:
        chunks.append(chunk)
        if len(chunks) >= count:
            break
    return chunks


def _blocks(chunks: list[str]) -> list[dict[str, str]]:
    parsed = []
    for chunk in chunks:
        fields = {
            line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
            for line in chunk.strip().splitlines()
            if not line.startswith(":")
        }
        fields["data"] = json.loads(fields["data"])
        parsed.append(fields)
    return parsed


def test_replays_persisted_events_on_first_connect() -> None:
    source = FakeSource([(_event(0), _event(1))])

    blocks = _blocks(
        asyncio.run(
            _take(
                sse_stream(source, RUN_ID, last_event_id=-1, poll_interval=0.005,
                           heartbeat_interval=60.0),
                2,
            )
        )
    )

    assert [block["id"] for block in blocks] == ["1", "2"]
    assert all(block["event"] == "proposal_created" for block in blocks)
    assert all(block["data"]["run_id"] == str(RUN_ID) for block in blocks)
    assert source.calls == [-1]


def test_last_event_id_cursor_replays_only_later_events() -> None:
    source = FakeSource([(_event(2),)])  # cursor = sequence + 1 = 3

    blocks = _blocks(
        asyncio.run(
            _take(
                sse_stream(source, RUN_ID, last_event_id=2, poll_interval=0.005,
                           heartbeat_interval=60.0),
                1,
            )
        )
    )

    assert [block["id"] for block in blocks] == ["3"]
    assert source.calls == [2]


def test_idle_stream_emits_heartbeat_comments() -> None:
    source = FakeSource([])

    chunks = asyncio.run(
        _take(
            sse_stream(source, RUN_ID, last_event_id=-1, poll_interval=0.005,
                       heartbeat_interval=0.02),
            1,
        )
    )

    assert chunks == [": heartbeat\n\n"]


def test_parse_last_event_id() -> None:
    assert parse_last_event_id(None) == -1
    assert parse_last_event_id("5") == 5
    assert parse_last_event_id("0") == 0
    with pytest.raises(ValueError):
        parse_last_event_id("not-a-number")
