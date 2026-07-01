"""Storage for Stripe subscriptions and webhook event idempotency."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from app.storage.db import IS_PG, open_db
from app.utils import now_iso as _now

_ACTIVE_STATUSES_EXCLUDED = ("canceled", "incomplete_expired")


class BillingStorage:
    """DB-backed subscription + webhook-event storage. Accepts the DB file path."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    async def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM subscriptions WHERE username = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (username,),
            )
            return dict(row) if row else None

    async def get_active_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        row = await self.get_by_username(username)
        if row and row["status"] not in _ACTIVE_STATUSES_EXCLUDED:
            return row
        return None

    async def get_by_stripe_subscription_id(self, stripe_subscription_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM subscriptions WHERE stripe_subscription_id = ?",
                (stripe_subscription_id,),
            )
            return dict(row) if row else None

    async def upsert(
        self,
        *,
        username: str,
        stripe_customer_id: str,
        stripe_subscription_id: str,
        tier: str,
        seats: int,
        self_hosted: bool,
        interval: str,
        amount_cents: int,
        status: str,
        current_period_end: Optional[str],
        cancel_at_period_end: bool,
    ) -> Dict[str, Any]:
        now = _now()
        existing = await self.get_by_stripe_subscription_id(stripe_subscription_id)
        async with open_db() as conn:
            if existing:
                await conn.execute(
                    "UPDATE subscriptions SET username=?, stripe_customer_id=?, tier=?, "
                    "seats=?, self_hosted=?, interval=?, amount_cents=?, status=?, "
                    "current_period_end=?, cancel_at_period_end=?, updated_at=? "
                    "WHERE stripe_subscription_id=?",
                    (
                        username,
                        stripe_customer_id,
                        tier,
                        seats,
                        1 if self_hosted else 0,
                        interval,
                        amount_cents,
                        status,
                        current_period_end,
                        1 if cancel_at_period_end else 0,
                        now,
                        stripe_subscription_id,
                    ),
                )
                await conn.commit()
                row_id = existing["id"]
            else:
                row_id = uuid4().hex[:16]
                await conn.execute(
                    "INSERT INTO subscriptions (id, username, stripe_customer_id, "
                    "stripe_subscription_id, tier, seats, self_hosted, interval, "
                    "amount_cents, status, current_period_end, cancel_at_period_end, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row_id,
                        username,
                        stripe_customer_id,
                        stripe_subscription_id,
                        tier,
                        seats,
                        1 if self_hosted else 0,
                        interval,
                        amount_cents,
                        status,
                        current_period_end,
                        1 if cancel_at_period_end else 0,
                        now,
                        now,
                    ),
                )
                await conn.commit()
        return await self.get_by_stripe_subscription_id(stripe_subscription_id)  # type: ignore[return-value]

    async def set_cancel_at_period_end(self, stripe_subscription_id: str, cancel: bool) -> None:
        async with open_db() as conn:
            await conn.execute(
                "UPDATE subscriptions SET cancel_at_period_end=?, updated_at=? "
                "WHERE stripe_subscription_id=?",
                (1 if cancel else 0, _now(), stripe_subscription_id),
            )
            await conn.commit()

    async def has_processed_event(self, stripe_event_id: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT 1 FROM stripe_events WHERE stripe_event_id = ?",
                (stripe_event_id,),
            )
            return row is not None

    async def record_event(self, stripe_event_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        async with open_db() as conn:
            if IS_PG:
                await conn.execute(
                    "INSERT INTO stripe_events (stripe_event_id, type, processed_at, payload) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (stripe_event_id) DO NOTHING",
                    (stripe_event_id, event_type, _now(), json.dumps(payload, ensure_ascii=False)),
                )
            else:
                await conn.execute(
                    "INSERT OR IGNORE INTO stripe_events (stripe_event_id, type, processed_at, payload) "
                    "VALUES (?, ?, ?, ?)",
                    (stripe_event_id, event_type, _now(), json.dumps(payload, ensure_ascii=False)),
                )
            await conn.commit()
