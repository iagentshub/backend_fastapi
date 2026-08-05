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
    assert await storage.has_processed_event("evt_1") is False
    await storage.record_event("evt_1", "customer.subscription.updated", {"id": "evt_1"})
    assert await storage.has_processed_event("evt_1") is True
    # Recording the same event id again must not raise (duplicate delivery from Stripe)
    await storage.record_event("evt_1", "customer.subscription.updated", {"id": "evt_1"})
