"""Storage for Stripe subscriptions and webhook event idempotency."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.storage.db import IS_PG, open_db
from app.utils import now_iso as _now
from app.utils.generators import generate_id

_ACTIVE_STATUSES_EXCLUDED = ("canceled", "incomplete_expired")


class BillingStorage:
    """Suscripciones y eventos de webhook en la base de datos configurada."""

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

    async def get_active_by_id(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
            )
            if row and row["status"] not in _ACTIVE_STATUSES_EXCLUDED:
                return dict(row)
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
                row_id = generate_id(16)
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

    async def ensure_owner_license(self, subscription: Dict[str, Any]) -> None:
        """Ensure the buyer consumes one license for active paid subscriptions."""
        if subscription["status"] in _ACTIVE_STATUSES_EXCLUDED:
            return
        await self.assign_license(
            subscription_id=subscription["id"],
            target_username=subscription["username"],
            assigned_by=subscription["username"],
            allow_existing=True,
        )

    async def assigned_count(self, subscription_id: str) -> int:
        async with open_db() as conn:
            return int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM subscription_license_assignments "
                    "WHERE subscription_id = ? AND status = 'active'",
                    (subscription_id,),
                )
                or 0
            )

    async def has_active_license(self, username: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT 1 FROM subscription_license_assignments la "
                "JOIN subscriptions s ON s.id = la.subscription_id "
                "WHERE la.username = ? AND la.status = 'active' "
                "AND s.status NOT IN (?, ?) LIMIT 1",
                (username, *_ACTIVE_STATUSES_EXCLUDED),
            )
            if row:
                return True
        # Backward-compatible fallback for subscription owners created before
        # the assignment table existed.
        return await self.get_active_by_username(username) is not None

    async def license_summary_for_owner(self, owner_username: str) -> Dict[str, Any]:
        subscription = await self.get_active_by_username(owner_username)
        if not subscription:
            return {
                "tier": "free",
                "seats": 0,
                "used": 0,
                "available": 0,
                "owner": owner_username,
                "assignments": [],
                "users": [],
            }

        await self.ensure_owner_license(subscription)
        subscription_id = subscription["id"]

        async with open_db() as conn:
            assignment_rows = await conn.fetchall(
                "SELECT la.username AS user_id, u.username, la.assigned_by, la.assigned_at, la.status, "
                "u.email, u.role, u.is_active "
                "FROM subscription_license_assignments la "
                "LEFT JOIN users u ON u.id = la.username "
                "WHERE la.subscription_id = ? "
                "ORDER BY la.status ASC, la.assigned_at ASC",
                (subscription_id,),
            )
            user_rows = await conn.fetchall(
                "SELECT id, username, email, role, is_active FROM users ORDER BY username ASC"
            )

        active = {
            row["user_id"]: dict(row)
            for row in assignment_rows
            if row["status"] == "active"
        }
        used = len(active)
        seats = int(subscription["seats"] or 0)
        return {
            "subscription_id": subscription_id,
            "owner": next((r["username"] for r in user_rows if r["id"] == owner_username), owner_username),
            "tier": subscription["tier"],
            "seats": seats,
            "used": used,
            "available": max(0, seats - used),
            "status": subscription["status"],
            "current_period_end": subscription["current_period_end"],
            "assignments": [dict(r) for r in assignment_rows],
            "users": [
                {
                    "username": r["username"],
                    "email": r["email"],
                    "role": r["role"],
                    "is_active": bool(r["is_active"]),
                    "licensed": r["id"] in active,
                }
                for r in user_rows
            ],
        }

    async def assign_license(
        self,
        *,
        subscription_id: str,
        target_username: str,
        assigned_by: str,
        allow_existing: bool = False,
    ) -> Dict[str, Any]:
        subscription = await self.get_active_by_id(subscription_id)
        if not subscription:
            raise ValueError("subscription_not_active")

        now = _now()
        async with open_db() as conn:
            if not await conn.fetchone(
                "SELECT 1 FROM users WHERE id = ?", (target_username,)
            ):
                raise ValueError("user_not_found")

            existing = await conn.fetchone(
                "SELECT * FROM subscription_license_assignments "
                "WHERE username = ? AND status = 'active'",
                (target_username,),
            )
            if existing:
                if allow_existing and existing["subscription_id"] == subscription_id:
                    return dict(existing)
                raise ValueError("license_already_assigned")

            used = int(
                await conn.fetchval(
                    "SELECT COUNT(*) FROM subscription_license_assignments "
                    "WHERE subscription_id = ? AND status = 'active'",
                    (subscription_id,),
                )
                or 0
            )
            if used >= int(subscription["seats"] or 0):
                raise ValueError("no_seats_available")

            if IS_PG:
                await conn.execute(
                    "INSERT INTO subscription_license_assignments "
                    "(subscription_id, username, assigned_by, assigned_at, status) "
                    "VALUES (?, ?, ?, ?, 'active') "
                    "ON CONFLICT (subscription_id, username) DO UPDATE SET "
                    "assigned_by = EXCLUDED.assigned_by, assigned_at = EXCLUDED.assigned_at, status = 'active'",
                    (subscription_id, target_username, assigned_by, now),
                )
            else:
                await conn.execute(
                    "INSERT OR REPLACE INTO subscription_license_assignments "
                    "(subscription_id, username, assigned_by, assigned_at, status) "
                    "VALUES (?, ?, ?, ?, 'active')",
                    (subscription_id, target_username, assigned_by, now),
                )
            await conn.commit()

            row = await conn.fetchone(
                "SELECT * FROM subscription_license_assignments "
                "WHERE subscription_id = ? AND username = ?",
                (subscription_id, target_username),
            )
            return dict(row)

    async def revoke_license(
        self, *, subscription_id: str, target_username: str
    ) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                "SELECT 1 FROM subscription_license_assignments "
                "WHERE subscription_id = ? AND username = ? AND status = 'active'",
                (subscription_id, target_username),
            )
            if not row:
                return False
            await conn.execute(
                "UPDATE subscription_license_assignments SET status = 'revoked' "
                "WHERE subscription_id = ? AND username = ?",
                (subscription_id, target_username),
            )
            await conn.commit()
            return True

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
