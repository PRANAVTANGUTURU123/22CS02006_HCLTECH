"""Remember which notifications were sent, across restarts (SQLAlchemy, portable SQL)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

metadata = MetaData()

sent_notifications = Table(
    "sent_notifications",
    metadata,
    Column("notification_id", String(200), primary_key=True),
    Column("sent_at", DateTime(timezone=True), nullable=False),
)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, pool_pre_ping=True)


class SqlSentStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        metadata.create_all(engine)

    def was_sent(self, notification_id: str) -> bool:
        query = select(sent_notifications.c.notification_id).where(
            sent_notifications.c.notification_id == notification_id
        )
        with self._engine.connect() as conn:
            return conn.execute(query).first() is not None

    def mark_sent(self, notification_id: str) -> None:
        row = {"notification_id": notification_id, "sent_at": datetime.now(UTC)}
        try:
            with self._engine.begin() as conn:
                conn.execute(insert(sent_notifications).values(**row))
        except IntegrityError:
            pass  # already recorded by another worker
