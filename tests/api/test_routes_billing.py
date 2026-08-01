"""Tests de rutas /api/billing/* — Stripe mockeado."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch

import app.api.routes.billing as billing_routes


class FakeStripeObject(dict):
    """Minimal stand-in for Stripe SDK objects: supports both dict and attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def _fake_subscription(
    *,
    sub_id="sub_123",
    customer="cus_123",
    status="incomplete",
    tier="developer",
    seats=1,
    interval="month",
    self_hosted=False,
    cancel_at_period_end=False,
    price_amount=900,
):
    payment_intent = FakeStripeObject(client_secret="pi_secret_abc")
    latest_invoice = FakeStripeObject(payment_intent=payment_intent)
    return FakeStripeObject(
        id=sub_id,
        customer=customer,
        status=status,
        cancel_at_period_end=cancel_at_period_end,
        latest_invoice=latest_invoice,
        current_period_end=1893456000,
        metadata={
            "username": "alice",
            "tier": tier,
            "seats": str(seats),
            "interval": interval,
            "self_hosted": "1" if self_hosted else "0",
        },
        items=FakeStripeObject(
            data=[FakeStripeObject(id="si_1", price=FakeStripeObject(unit_amount=price_amount))]
        ),
    )


def _setup_user(client, username="alice"):
    from app.auth.auth import create_token, register_user
    asyncio.run(register_user(username, "pass1234", email=f"{username}@example.com"))
    token = create_token(username)
    client.cookies.set("ga_token", token)
    return username


def _login_as(client, username):
    from app.auth.auth import create_token
    client.cookies.set("ga_token", create_token(username))


def _user_id(username: str) -> str:
    from app.auth.auth import get_user_by_username

    return asyncio.run(get_user_by_username(username))["id"]


def _enable_billing():
    import app.config.data as cfg
    cfg.SETTINGS_FILE.write_text(
        json.dumps({"jwt_secret": "test-secret-key-for-tests-only", "billing_enabled": True}),
        encoding="utf-8",
    )


# ── /quote ──────────────────────────────────────────────────────────────────

def test_quote_developer(client):
    r = client.post("/api/billing/quote", json={"tier": "developer", "seats": 1, "interval": "month", "self_hosted": False})
    assert r.status_code == 200
    assert r.json()["amount_cents"] == 900


def test_quote_business_decreasing(client):
    r10 = client.post("/api/billing/quote", json={"tier": "business", "seats": 10, "interval": "month", "self_hosted": False})
    r50 = client.post("/api/billing/quote", json={"tier": "business", "seats": 50, "interval": "month", "self_hosted": False})
    assert r10.status_code == 200 and r50.status_code == 200
    per_seat_10 = r10.json()["price_per_seat_cents"]
    per_seat_50 = r50.json()["price_per_seat_cents"]
    assert per_seat_10 > per_seat_50


def test_quote_invalid_seats(client):
    r = client.post("/api/billing/quote", json={"tier": "developer", "seats": 2, "interval": "month", "self_hosted": False})
    assert r.status_code == 400


# ── /subscribe ──────────────────────────────────────────────────────────────

def test_subscribe_requires_auth(client):
    r = client.post("/api/billing/subscribe", json={"tier": "developer", "seats": 1, "interval": "month"})
    assert r.status_code == 401


def test_subscribe_success_creates_customer_and_subscription(client):
    _setup_user(client, "alice")
    fake_sub = _fake_subscription()
    with patch.object(billing_routes.stripe.Customer, "create", return_value=FakeStripeObject(id="cus_123")) as mock_customer, \
         patch.object(billing_routes.stripe.Subscription, "create", return_value=fake_sub) as mock_sub:
        r = client.post("/api/billing/subscribe", json={"tier": "developer", "seats": 1, "interval": "month", "self_hosted": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subscription_id"] == "sub_123"
    assert body["client_secret"] == "pi_secret_abc"
    mock_customer.assert_called_once()
    mock_sub.assert_called_once()

    state = client.get("/api/billing/subscription").json()
    assert state["tier"] == "developer"
    assert state["seats"] == 1


def test_subscribe_seats_out_of_range(client):
    _setup_user(client, "alice")
    r = client.post("/api/billing/subscribe", json={"tier": "business", "seats": 1, "interval": "month"})
    assert r.status_code == 400


def test_subscribe_duplicate_active_subscription_409(client):
    _setup_user(client, "alice")
    fake_sub = _fake_subscription()
    with patch.object(billing_routes.stripe.Customer, "create", return_value=FakeStripeObject(id="cus_123")), \
         patch.object(billing_routes.stripe.Subscription, "create", return_value=fake_sub):
        r1 = client.post("/api/billing/subscribe", json={"tier": "developer", "seats": 1, "interval": "month"})
        assert r1.status_code == 200
        r2 = client.post("/api/billing/subscribe", json={"tier": "developer", "seats": 1, "interval": "month"})
    assert r2.status_code == 409


# ── GET /subscription ─────────────────────────────────────────────────────────

def test_get_subscription_free_default(client):
    _setup_user(client, "alice")
    r = client.get("/api/billing/subscription")
    assert r.status_code == 200
    assert r.json()["tier"] == "free"


# ── /cancel, /reactivate ──────────────────────────────────────────────────────

def _subscribe(client, **plan):
    billing_routes._subscribe_limiter._data.clear()
    fake_sub = _fake_subscription(**{k: v for k, v in plan.items() if k in ("tier", "seats", "interval", "self_hosted")})
    with patch.object(billing_routes.stripe.Customer, "create", return_value=FakeStripeObject(id="cus_123")), \
         patch.object(billing_routes.stripe.Subscription, "create", return_value=fake_sub):
        r = client.post("/api/billing/subscribe", json={
            "tier": plan.get("tier", "developer"),
            "seats": plan.get("seats", 1),
            "interval": plan.get("interval", "month"),
            "self_hosted": plan.get("self_hosted", False),
        })
    assert r.status_code == 200, r.text
    return r.json()


def test_cancel_no_subscription_404(client):
    _setup_user(client, "alice")
    r = client.post("/api/billing/cancel", json={"immediate": False})
    assert r.status_code == 404


def test_cancel_graceful_sets_cancel_at_period_end(client):
    _setup_user(client, "alice")
    _subscribe(client)
    with patch.object(billing_routes.stripe.Subscription, "modify", return_value=None) as mock_modify:
        r = client.post("/api/billing/cancel", json={"immediate": False})
    assert r.status_code == 200
    assert r.json()["cancel_at_period_end"] is True
    mock_modify.assert_called_once()


def test_reactivate_after_graceful_cancel(client):
    _setup_user(client, "alice")
    _subscribe(client)
    with patch.object(billing_routes.stripe.Subscription, "modify", return_value=None):
        client.post("/api/billing/cancel", json={"immediate": False})
    with patch.object(billing_routes.stripe.Subscription, "modify", return_value=None) as mock_modify:
        r = client.post("/api/billing/reactivate")
    assert r.status_code == 200
    assert r.json()["cancel_at_period_end"] is False
    mock_modify.assert_called_once()


def test_reactivate_without_pending_cancel_400(client):
    _setup_user(client, "alice")
    _subscribe(client)
    r = client.post("/api/billing/reactivate")
    assert r.status_code == 400


# ── /change-seats ──────────────────────────────────────────────────────────────

def test_change_seats_business(client):
    _setup_user(client, "alice")
    _subscribe(client, tier="business", seats=10, interval="month")
    fake_sub = _fake_subscription(tier="business", seats=10)
    with patch.object(billing_routes.stripe.Subscription, "retrieve", return_value=fake_sub), \
         patch.object(billing_routes.stripe.SubscriptionItem, "modify", return_value=None) as mock_item_modify:
        r = client.post("/api/billing/change-seats", json={"seats": 20})
    assert r.status_code == 200, r.text
    assert r.json()["seats"] == 20
    mock_item_modify.assert_called_once()


def test_change_seats_rejected_for_developer_tier(client):
    _setup_user(client, "alice")
    _subscribe(client, tier="developer", seats=1)
    r = client.post("/api/billing/change-seats", json={"seats": 5})
    assert r.status_code == 400


# ── /licenses + license gate ─────────────────────────────────────────────────

def test_business_subscription_auto_assigns_owner_license(client):
    _setup_user(client, "alice")
    _subscribe(client, tier="business", seats=3)

    r = client.get("/api/billing/licenses")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["tier"] == "business"
    assert data["seats"] == 3
    assert data["used"] == 1
    assert data["available"] == 2
    assert any(u["username"] == "alice" and u["licensed"] for u in data["users"])


def test_assigning_licenses_respects_seat_limit_and_revoke_frees_slot(client):
    _setup_user(client, "alice")
    _setup_user(client, "bobby")
    _setup_user(client, "carol")
    _setup_user(client, "david")
    _login_as(client, "alice")
    _subscribe(client, tier="business", seats=3)

    assert client.post("/api/billing/licenses/bobby", json={}).status_code == 200
    assert client.post("/api/billing/licenses/carol", json={}).status_code == 200
    over = client.post("/api/billing/licenses/david", json={})
    assert over.status_code == 409

    revoked = client.delete("/api/billing/licenses/bobby")
    assert revoked.status_code == 200
    assert revoked.json()["available"] == 1
    assert client.post("/api/billing/licenses/david", json={}).status_code == 200


def test_non_owner_cannot_assign_licenses(client):
    _setup_user(client, "alice")
    _setup_user(client, "bobby")
    _login_as(client, "alice")
    _subscribe(client, tier="business", seats=3)

    _login_as(client, "bobby")
    r = client.post("/api/billing/licenses/bobby", json={})
    assert r.status_code == 404


def test_license_gate_blocks_standard_user_without_license(client):
    _enable_billing()
    _setup_user(client, "alice")
    r = client.get("/api/connections")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "license_required"


def test_license_gate_allogroup_assigned_user(client):
    _enable_billing()
    _setup_user(client, "alice")
    _setup_user(client, "bobby")
    _login_as(client, "alice")
    _subscribe(client, tier="business", seats=3)
    client.post("/api/billing/licenses/bobby", json={})

    _login_as(client, "bobby")
    r = client.get("/api/connections")
    assert r.status_code == 200


def test_canceled_subscription_license_does_not_grant_access(client):
    _enable_billing()
    _setup_user(client, "alice")
    _setup_user(client, "bobby")
    _login_as(client, "alice")
    _subscribe(client, tier="business", seats=3)
    client.post("/api/billing/licenses/bobby", json={})
    row = asyncio.run(billing_routes._billing.get_active_by_username(_user_id("alice")))
    asyncio.run(
        billing_routes._billing.upsert(
            username="alice",
            stripe_customer_id=row["stripe_customer_id"],
            stripe_subscription_id=row["stripe_subscription_id"],
            tier=row["tier"],
            seats=row["seats"],
            self_hosted=bool(row["self_hosted"]),
            interval=row["interval"],
            amount_cents=row["amount_cents"],
            status="canceled",
            current_period_end=row["current_period_end"],
            cancel_at_period_end=True,
        )
    )

    _login_as(client, "bobby")
    r = client.get("/api/connections")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "license_required"


# ── /webhook ───────────────────────────────────────────────────────────────────

_WEBHOOK_SECRET = "whsec_test_secret"


def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload_bytes.decode()}"
    sig = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


def test_webhook_invalid_signature_400(client):
    body = json.dumps({"id": "evt_1", "type": "customer.subscription.updated", "data": {"object": {}}}).encode()
    r = client.post("/api/billing/webhook", content=body, headers={"stripe-signature": "t=1,v1=bad"})
    assert r.status_code == 400


def test_webhook_real_signature_and_subscription_updated(client):
    _setup_user(client, "alice")
    sub_obj = {
        "id": "sub_999",
        "customer": "cus_999",
        "status": "active",
        "cancel_at_period_end": False,
        "current_period_end": 1893456000,
        "metadata": {"username": "alice", "tier": "developer", "seats": "1", "interval": "month", "self_hosted": "0"},
        "items": {"data": [{"id": "si_1", "price": {"id": "price_x"}}]},
    }
    event = {"id": "evt_real_1", "object": "event", "type": "customer.subscription.updated", "data": {"object": sub_obj}}
    payload = json.dumps(event).encode()

    with patch.object(billing_routes, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        sig_header = _sign_payload(payload, _WEBHOOK_SECRET)
        r = client.post("/api/billing/webhook", content=payload, headers={"stripe-signature": sig_header})

    assert r.status_code == 200, r.text
    state = client.get("/api/billing/subscription").json()
    assert state["tier"] == "developer"
    assert state["status"] == "active"


def test_webhook_duplicate_event_processed_once(client):
    _setup_user(client, "alice")
    sub_obj = {
        "id": "sub_dup",
        "customer": "cus_dup",
        "status": "active",
        "cancel_at_period_end": False,
        "current_period_end": 1893456000,
        "metadata": {"username": "alice", "tier": "developer", "seats": "1", "interval": "month", "self_hosted": "0"},
        "items": {"data": [{"id": "si_1", "price": {"id": "price_x"}}]},
    }
    event = {"id": "evt_dup_1", "object": "event", "type": "customer.subscription.updated", "data": {"object": sub_obj}}
    payload = json.dumps(event).encode()

    with patch.object(billing_routes, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        sig_header = _sign_payload(payload, _WEBHOOK_SECRET)
        with patch.object(billing_routes, "_handle_subscription_event", new_callable=AsyncMock) as mock_handle:
            client.post("/api/billing/webhook", content=payload, headers={"stripe-signature": sig_header})
            client.post("/api/billing/webhook", content=payload, headers={"stripe-signature": sig_header})
        assert mock_handle.call_count == 1


def test_webhook_subscription_deleted_marks_canceled(client):
    _setup_user(client, "alice")
    _subscribe(client)
    row = asyncio.run(billing_routes._billing.get_active_by_username(_user_id("alice")))
    sub_obj = {
        "id": row["stripe_subscription_id"],
        "customer": row["stripe_customer_id"],
        "status": "canceled",
        "cancel_at_period_end": True,
        "current_period_end": 1893456000,
        "metadata": {"username": "alice", "tier": "developer", "seats": "1", "interval": "month", "self_hosted": "0"},
        "items": {"data": [{"id": "si_1", "price": {"id": "price_x"}}]},
    }
    event = {"id": "evt_del_1", "object": "event", "type": "customer.subscription.deleted", "data": {"object": sub_obj}}
    payload = json.dumps(event).encode()

    with patch.object(billing_routes, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        sig_header = _sign_payload(payload, _WEBHOOK_SECRET)
        r = client.post("/api/billing/webhook", content=payload, headers={"stripe-signature": sig_header})
    assert r.status_code == 200

    state = client.get("/api/billing/subscription").json()
    assert state["tier"] == "free"  # canceled -> excluded from "active"
