"""Vínculo entre el username local y el customer id de Stripe."""

from __future__ import annotations

from typing import Optional

from app.sql import sql
from app.storage.db import open_db
from app.utils.validation import normalize_username


async def get_stripe_customer_id(username: str) -> Optional[str]:
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/billing_link:stripe_customer_of"),
            (username, normalize_username(username)),
        )
        return row["stripe_customer_id"] if row else None


async def set_stripe_customer_id(username: str, customer_id: str) -> None:
    async with open_db() as conn:
        await conn.execute(
            sql("queries/billing_link:set_stripe_customer"),
            (customer_id, username, normalize_username(username)),
        )
        await conn.commit()


async def get_username_by_stripe_customer_id(customer_id: str) -> Optional[str]:
    async with open_db() as conn:
        row = await conn.fetchone(
            sql("queries/billing_link:user_by_stripe_customer"), (customer_id,)
        )
        return row["id"] if row else None
