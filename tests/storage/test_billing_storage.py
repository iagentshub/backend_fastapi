"""Tests de BillingStorage — suscripciones e idempotencia de webhooks."""
from __future__ import annotations

import pytest

from app.storage.billing import BillingStorage


@pytest.fixture()
async def storage(patch_data_dir):
    return BillingStorage()


def _sub_kwargs(**overrides):
    base = dict(
        username="alice",
        stripe_customer_id="cus_1",
        stripe_subscription_id="sub_1",
        tier="developer",
        seats=1,
        self_hosted=False,
        interval="month",
        amount_cents=900,
        status="active",
        current_period_end="2026-08-01T00:00:00+00:00",
        cancel_at_period_end=False,
    )
    base.update(overrides)
    return base


async def test_get_by_username_empty(storage):
    assert await storage.get_by_username("alice") is None


async def test_upsert_insert(storage):
    row = await storage.upsert(**_sub_kwargs())
    assert row["username"] == "alice"
    assert row["tier"] == "developer"
    assert row["status"] == "active"


async def test_upsert_update_existing(storage):
    await storage.upsert(**_sub_kwargs())
    updated = await storage.upsert(**_sub_kwargs(status="past_due"))
    assert updated["status"] == "past_due"
    all_rows = await storage.get_by_username("alice")
    assert all_rows["status"] == "past_due"


async def test_get_active_by_username_excludes_canceled(storage):
    await storage.upsert(**_sub_kwargs(status="canceled"))
    assert await storage.get_active_by_username("alice") is None


async def test_get_active_by_username_returns_active(storage):
    await storage.upsert(**_sub_kwargs(status="active"))
    row = await storage.get_active_by_username("alice")
    assert row is not None
    assert row["stripe_subscription_id"] == "sub_1"


async def test_get_by_stripe_subscription_id(storage):
    await storage.upsert(**_sub_kwargs())
    row = await storage.get_by_stripe_subscription_id("sub_1")
    assert row is not None
    assert row["username"] == "alice"


async def test_set_cancel_at_period_end(storage):
    await storage.upsert(**_sub_kwargs())
    await storage.set_cancel_at_period_end("sub_1", True)
    row = await storage.get_by_stripe_subscription_id("sub_1")
    assert row["cancel_at_period_end"] == 1


async def test_event_idempotency(storage):
    """La reserva la gana uno solo, y soltarla devuelve el evento al ruedo."""
    evento = ("evt_1", "customer.subscription.updated", {"id": "evt_1"})
    assert await storage.claim_event(*evento) is True
    # La entrega duplicada de Stripe choca contra el PRIMARY KEY y no reserva.
    assert await storage.claim_event(*evento) is False

    await storage.discard_event("evt_1")
    assert await storage.claim_event(*evento) is True


async def test_purge_events_respeta_la_retencion(storage):
    """Era la única tabla del esquema de la que no se borraba nunca nada."""
    from datetime import datetime, timedelta, timezone

    from app.sql import sql
    from app.storage.db import open_db

    await storage.claim_event("evt_reciente", "customer.subscription.updated", {})
    # Un evento viejo se escribe a mano: `claim_event` siempre pone la hora
    # actual, y lo que se quiere ejercer aquí es justo el corte por fecha.
    antiguo = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    async with open_db() as conn:
        # `fetchone` y no `execute`: la consulta lleva RETURNING, y un cursor
        # sin consumir deja la sentencia en curso y hace fallar el commit.
        await conn.fetchone(
            sql("queries/billing:claim_stripe_event_sqlite"),
            ("evt_viejo", "customer.subscription.updated", antiguo, "{}"),
        )
        await conn.commit()

    assert await storage.purge_events(365) == 1
    # El viejo se fue y el reciente sigue: su id no se puede volver a reservar.
    assert await storage.claim_event("evt_viejo", "customer.subscription.updated", {}) is True
    assert await storage.claim_event("evt_reciente", "customer.subscription.updated", {}) is False
