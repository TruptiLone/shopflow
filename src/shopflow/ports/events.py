"""Domain events via the transactional outbox.

`record_event` must be called inside the same DB transaction as the state change it
describes. That guarantees an event exists if and only if the change committed. The
queue/Kafka phase adds a relay that reads unpublished rows and publishes them
(at-least-once; consumers dedupe on event id)."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from shopflow.domain.models import OutboxEvent


async def record_event(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        OutboxEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
        )
    )
