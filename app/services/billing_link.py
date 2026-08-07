"""Vínculo entre el username local y el customer id de Stripe."""

from __future__ import annotations

from typing import Optional

from app.storage.db import open_db
from app.utils.validation import normalize_username


async def get_stripe_customer_id(username: str) -> Optional[str]:
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT stripe_customer_id FROM users WHERE id = ? OR username = ?",
            (username, normalize_username(username)),
        )
        return row["stripe_customer_id"] if row else None


async def set_stripe_customer_id(username: str, customer_id: str) -> None:
    async with open_db() as conn:
        await conn.execute(
            "UPDATE users SET stripe_customer_id = ? WHERE id = ? OR username = ?",
            (customer_id, username, normalize_username(username)),
        )
        await conn.commit()


async def get_username_by_stripe_customer_id(customer_id: str) -> Optional[str]:
    async with open_db() as conn:
        row = await conn.fetchone(
            "SELECT id FROM users WHERE stripe_customer_id = ?", (customer_id,)
        )
        return row["id"] if row else None
