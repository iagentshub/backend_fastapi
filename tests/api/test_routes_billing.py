"""Tests de rutas /api/billing/* — Stripe mockeado."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
import stripe

import app.api.routes.billing._shared as billing_routes
import app.api.routes.billing.webhook as billing_webhook


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
    subtotal=900,
    tax=189,
):
    payment_intent = FakeStripeObject(client_secret="pi_secret_abc")
    # La factura la crea Stripe junto con la suscripción (default_incomplete) y
    # ya trae el impuesto calculado: de ahí sale el desglose que ve el checkout.
    latest_invoice = FakeStripeObject(
        payment_intent=payment_intent,
        subtotal=subtotal,
        tax=tax,
        total=subtotal + tax,
    )
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


@contextlib.contextmanager
def _stripe_alta(subscription=None, customer_id="cus_123", tax_ids=()):
    """Las cuatro llamadas a Stripe que hace un alta, mockeadas juntas.

    `Customer.modify` y `list_tax_ids` entraron con el IVA: el país de
    facturación se escribe en el cliente antes de crear la suscripción, porque
    sin ubicación Stripe no puede calcular el impuesto.
    """
    fake_sub = subscription if subscription is not None else _fake_subscription()
    with patch.object(
        stripe.Customer, "create", return_value=FakeStripeObject(id=customer_id)
    ) as create_customer, patch.object(
        stripe.Customer, "modify", return_value=FakeStripeObject(id=customer_id)
    ) as modify_customer, patch.object(
        stripe.Customer,
        "list_tax_ids",
        return_value=FakeStripeObject(
            data=[FakeStripeObject(value=v) for v in tax_ids]
        ),
    ), patch.object(
        stripe.Customer, "create_tax_id", return_value=FakeStripeObject(id="txi_1")
    ) as create_tax_id, patch.object(
        stripe.Subscription, "create", return_value=fake_sub
    ) as create_sub:
        yield {
            "customer": create_customer,
            "modify": modify_customer,
            "tax_id": create_tax_id,
            "subscription": create_sub,
        }


def _plan(**overrides):
    """Cuerpo válido de /subscribe. El país es obligatorio desde el IVA."""
    body = {
        "tier": "developer",
        "seats": 1,
        "interval": "month",
        "self_hosted": False,
        "country": "ES",
    }
    body.update(overrides)
    return body


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
    with _stripe_alta() as mocks:
        r = client.post("/api/billing/subscribe", json=_plan())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["subscription_id"] == "sub_123"
    assert body["client_secret"] == "pi_secret_abc"
    mocks["customer"].assert_called_once()
    mocks["subscription"].assert_called_once()

    state = client.get("/api/billing/subscription").json()
    assert state["tier"] == "developer"
    assert state["seats"] == 1


def test_subscribe_seats_out_of_range(client):
    _setup_user(client, "alice")
    r = client.post("/api/billing/subscribe", json=_plan(tier="business", seats=1))
    assert r.status_code == 400


def test_subscribe_duplicate_active_subscription_409(client):
    _setup_user(client, "alice")
    with _stripe_alta(_fake_subscription(status="active")):
        r1 = client.post("/api/billing/subscribe", json=_plan())
        assert r1.status_code == 200
        r2 = client.post("/api/billing/subscribe", json=_plan())
    assert r2.status_code == 409


def test_un_alta_sin_pagar_no_bloquea_el_siguiente_intento(client):
    """Cambiar de país (o volver tras abandonar) tiene que poder reintentarse.

    Una suscripción `incomplete` es un alta creada y nunca pagada, pero contaba
    como activa: el segundo intento chocaba con un 409 que hablaba de una
    suscripción que el usuario nunca tuvo. Además su factura ya lleva el
    impuesto del país anterior, así que no se puede reutilizar.
    """
    _setup_user(client, "alice")
    with _stripe_alta():
        assert client.post("/api/billing/subscribe", json=_plan()).status_code == 200
        with patch.object(stripe.Subscription, "delete") as mock_delete:
            r2 = client.post("/api/billing/subscribe", json=_plan(country="FR"))
    assert r2.status_code == 200, r2.text
    mock_delete.assert_called_once_with("sub_123")


def test_si_stripe_no_puede_cancelar_la_incompleta_el_alta_sigue(client):
    """Que ya no exista al otro lado no puede dejar al usuario sin poder pagar."""
    _setup_user(client, "alice")
    with _stripe_alta():
        assert client.post("/api/billing/subscribe", json=_plan()).status_code == 200
        with patch.object(
            stripe.Subscription,
            "delete",
            side_effect=stripe.error.InvalidRequestError("No such subscription", param="id"),
        ):
            r2 = client.post("/api/billing/subscribe", json=_plan(country="FR"))
    assert r2.status_code == 200, r2.text


# ── IVA (FIN-01) ─────────────────────────────────────────────────────────────
# Hasta aquí se cobraba el importe neto: ni se repercutía IVA ni había forma de
# declarar el NIF de una empresa de otro Estado miembro. Estos tests fijan las
# dos direcciones — que el impuesto se calcule y que el desglose llegue al
# cliente antes de pagar.


def test_subscribe_exige_pais_de_facturacion(client):
    """Sin país, Stripe no puede calcular el impuesto: se corta antes de llamar."""
    _setup_user(client, "alice")
    with _stripe_alta() as mocks:
        r = client.post("/api/billing/subscribe", json=_plan(country=""))
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_field"
    assert detail["field"] == "country"
    mocks["subscription"].assert_not_called()


def test_subscribe_rechaza_pais_que_no_es_iso(client):
    _setup_user(client, "alice")
    with _stripe_alta() as mocks:
        r = client.post("/api/billing/subscribe", json=_plan(country="Es"))
    assert r.status_code == 200, r.text  # "Es" se normaliza a "ES"
    assert mocks["modify"].call_args.kwargs["address"] == {"country": "ES"}


def test_subscribe_activa_el_impuesto_automatico_y_declara_el_comportamiento(client):
    """automatic_tax sin tax_behavior en el precio es un error de Stripe entero."""
    _setup_user(client, "alice")
    with _stripe_alta() as mocks:
        r = client.post("/api/billing/subscribe", json=_plan())
    assert r.status_code == 200, r.text
    kwargs = mocks["subscription"].call_args.kwargs
    assert kwargs["automatic_tax"] == {"enabled": True}
    assert kwargs["items"][0]["price_data"]["tax_behavior"] == "exclusive"
    assert kwargs["metadata"]["country"] == "ES"


def test_subscribe_escribe_el_pais_en_el_cliente_de_stripe(client):
    """Es la dirección con la que se emite la factura, no solo un dato fiscal."""
    _setup_user(client, "alice")
    with _stripe_alta() as mocks:
        client.post("/api/billing/subscribe", json=_plan(country="FR"))
    mocks["modify"].assert_called_once()
    assert mocks["modify"].call_args.kwargs["address"] == {"country": "FR"}


def test_subscribe_registra_el_nif_intracomunitario(client):
    _setup_user(client, "alice")
    with _stripe_alta() as mocks:
        r = client.post("/api/billing/subscribe", json=_plan(tax_id="A1234567 J"))
    assert r.status_code == 200, r.text
    mocks["tax_id"].assert_called_once()
    kwargs = mocks["tax_id"].call_args.kwargs
    assert kwargs["type"] == "eu_vat"
    # Sin prefijo de país se le antepone el declarado, y se limpian espacios.
    assert kwargs["value"] == "ESA1234567J"


def test_subscribe_no_duplica_un_nif_ya_registrado(client):
    """Stripe admite el mismo NIF dos veces sin quejarse; la factura no."""
    _setup_user(client, "alice")
    with _stripe_alta(tax_ids=("ESA1234567J",)) as mocks:
        r = client.post("/api/billing/subscribe", json=_plan(tax_id="ESA1234567J"))
    assert r.status_code == 200, r.text
    mocks["tax_id"].assert_not_called()


def test_subscribe_rechaza_nif_de_fuera_de_la_ue(client):
    _setup_user(client, "alice")
    with _stripe_alta() as mocks:
        r = client.post("/api/billing/subscribe", json=_plan(country="US", tax_id="12-3456789"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "tax_id_country_unsupported"
    mocks["subscription"].assert_not_called()


def test_subscribe_sin_nif_no_llama_a_stripe_por_el_nif(client):
    """El NIF es opcional: sin él se cobra como consumidor, con IVA."""
    _setup_user(client, "alice")
    with _stripe_alta() as mocks:
        r = client.post("/api/billing/subscribe", json=_plan(country="US"))
    assert r.status_code == 200, r.text
    mocks["tax_id"].assert_not_called()


def test_subscribe_devuelve_el_desglose_de_la_factura(client):
    """Lo que se va a cobrar sale de Stripe, no de nuestra aritmética."""
    _setup_user(client, "alice")
    with _stripe_alta(_fake_subscription(subtotal=900, tax=189)):
        r = client.post("/api/billing/subscribe", json=_plan())
    body = r.json()
    assert body["subtotal_cents"] == 900
    assert body["tax_cents"] == 189
    assert body["total_cents"] == 1089


def test_subscribe_sin_totales_en_la_factura_cae_al_neto(client):
    """Con Stripe Tax apagado la factura no trae desglose: el total es el neto."""
    _setup_user(client, "alice")
    sub = _fake_subscription()
    sub["latest_invoice"] = FakeStripeObject(
        payment_intent=FakeStripeObject(client_secret="pi_secret_abc")
    )
    with _stripe_alta(sub):
        r = client.post("/api/billing/subscribe", json=_plan())
    body = r.json()
    assert body["subtotal_cents"] == 900
    assert body["tax_cents"] == 0
    assert body["total_cents"] == 900


def test_error_fiscal_de_stripe_es_400_del_cliente_no_502(client):
    """Un país que Stripe no sabe gravar lo corrige el usuario, no el servidor."""
    _setup_user(client, "alice")
    fallo = stripe.error.InvalidRequestError(
        "Customer tax location invalid", param=None, code="customer_tax_location_invalid"
    )
    with patch.object(stripe.Customer, "create", return_value=FakeStripeObject(id="cus_123")), \
         patch.object(stripe.Customer, "modify", side_effect=fallo):
        r = client.post("/api/billing/subscribe", json=_plan())
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_tax_location"
    assert detail["field"] == "country"


def test_error_de_stripe_que_no_es_del_cliente_sigue_siendo_502(client):
    _setup_user(client, "alice")
    fallo = stripe.error.InvalidRequestError("No such product", param="product")
    with patch.object(stripe.Customer, "create", return_value=FakeStripeObject(id="cus_123")), \
         patch.object(stripe.Customer, "modify", side_effect=fallo):
        r = client.post("/api/billing/subscribe", json=_plan())
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "upstream_error"


def test_quote_dice_que_el_precio_no_lleva_impuesto(client):
    """La página pública tiene que poder anunciar «IVA no incluido» sin duplicar la política."""
    r = client.post("/api/billing/quote", json={"tier": "developer", "seats": 1, "interval": "month"})
    assert r.status_code == 200
    assert r.json()["tax_behavior"] == "exclusive"


def test_change_seats_mantiene_el_tax_behavior(client):
    """Un precio nuevo sin él deja la suscripción sin poder facturar el periodo siguiente."""
    _setup_user(client, "alice")
    _subscribe(client, tier="business", seats=10, interval="month")
    with patch.object(stripe.Subscription, "retrieve", return_value=_fake_subscription(tier="business", seats=10)), \
         patch.object(stripe.SubscriptionItem, "modify", return_value=FakeStripeObject(id="si_1")) as mock_item:
        r = client.post("/api/billing/change-seats", json={"seats": 20})
    assert r.status_code == 200, r.text
    assert mock_item.call_args.kwargs["price_data"]["tax_behavior"] == "exclusive"


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
    with _stripe_alta(fake_sub):
        r = client.post("/api/billing/subscribe", json=_plan(
            tier=plan.get("tier", "developer"),
            seats=plan.get("seats", 1),
            interval=plan.get("interval", "month"),
            self_hosted=plan.get("self_hosted", False),
        ))
    assert r.status_code == 200, r.text
    return r.json()


def test_cancel_no_subscription_404(client):
    _setup_user(client, "alice")
    r = client.post("/api/billing/cancel", json={"immediate": False})
    assert r.status_code == 404


def test_cancel_graceful_sets_cancel_at_period_end(client):
    _setup_user(client, "alice")
    _subscribe(client)
    with patch.object(stripe.Subscription, "modify", return_value=None) as mock_modify:
        r = client.post("/api/billing/cancel", json={"immediate": False})
    assert r.status_code == 200
    assert r.json()["cancel_at_period_end"] is True
    mock_modify.assert_called_once()


def test_reactivate_after_graceful_cancel(client):
    _setup_user(client, "alice")
    _subscribe(client)
    with patch.object(stripe.Subscription, "modify", return_value=None):
        client.post("/api/billing/cancel", json={"immediate": False})
    with patch.object(stripe.Subscription, "modify", return_value=None) as mock_modify:
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
    with patch.object(stripe.Subscription, "retrieve", return_value=fake_sub), \
         patch.object(stripe.SubscriptionItem, "modify", return_value=None) as mock_item_modify:
        r = client.post("/api/billing/change-seats", json={"seats": 20})
    assert r.status_code == 200, r.text
    assert r.json()["seats"] == 20
    mock_item_modify.assert_called_once()


def test_change_seats_rejected_for_developer_tier(client):
    _setup_user(client, "alice")
    _subscribe(client, tier="developer", seats=1)
    r = client.post("/api/billing/change-seats", json={"seats": 5})
    assert r.status_code == 400


# ── /licenses (ruta histórica de asientos) + subscription gate ──────────────

def test_business_subscription_auto_assigns_owner_seat(client):
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


def test_assigning_seats_respects_limit_and_revoke_frees_slot(client):
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
    assert over.json()["detail"]["code"] == "no_seats_available"
    assert "asientos" in over.json()["detail"]["message"].lower()
    assert "licencia" not in over.json()["detail"]["message"].lower()

    revoked = client.delete("/api/billing/licenses/bobby")
    assert revoked.status_code == 200
    assert revoked.json()["available"] == 1
    assert client.post("/api/billing/licenses/david", json={}).status_code == 200


def test_non_owner_cannot_assign_seats(client):
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
    assert r.json()["detail"]["code"] == "subscription_required"


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
    assert r.json()["detail"]["code"] == "subscription_required"


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

    with patch.object(billing_webhook, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
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

    with patch.object(billing_webhook, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        sig_header = _sign_payload(payload, _WEBHOOK_SECRET)
        with patch.object(billing_webhook, "_handle_subscription_event", new_callable=AsyncMock) as mock_handle:
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

    with patch.object(billing_webhook, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        sig_header = _sign_payload(payload, _WEBHOOK_SECRET)
        r = client.post("/api/billing/webhook", content=payload, headers={"stripe-signature": sig_header})
    assert r.status_code == 200

    state = client.get("/api/billing/subscription").json()
    assert state["tier"] == "free"  # canceled -> excluded from "active"


def test_webhook_suscripcion_sin_usuario_deja_rastro(client):
    """Un cobro que no se puede atribuir ya no se descarta en silencio.

    Antes esta rama era un `return` mudo: el evento quedaba marcado como
    procesado, Stripe no lo reintentaba y no quedaba ni una línea. El único
    rastro era el payload en `stripe_events`, que no lee ningún endpoint.
    """
    sub_obj = {
        "id": "sub_huerfana",
        "customer": "cus_sin_enlazar",
        "status": "active",
        "cancel_at_period_end": False,
        "current_period_end": 1893456000,
        # Ni user_id ni el username heredado: el alta viene de fuera de la API.
        "metadata": {"tier": "developer", "seats": "1"},
        "items": {"data": [{"id": "si_1", "price": {"id": "price_x"}}]},
    }
    event = {"id": "evt_huerfano_1", "object": "event", "type": "customer.subscription.created", "data": {"object": sub_obj}}
    payload = json.dumps(event).encode()

    with patch.object(billing_webhook, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        sig_header = _sign_payload(payload, _WEBHOOK_SECRET)
        with patch.object(billing_webhook.flog, "audit") as mock_audit:
            r = client.post("/api/billing/webhook", content=payload, headers={"stripe-signature": sig_header})

    # 200 a propósito: un 5xx haría reintentar tres días por una causa que casi
    # nunca es transitoria, y arriesga que Stripe deshabilite el endpoint.
    assert r.status_code == 200, r.text

    mock_audit.assert_called_once()
    accion = mock_audit.call_args.args[0]
    kwargs = mock_audit.call_args.kwargs
    assert accion == "billing.webhook.unattributed"
    assert kwargs["outcome"] == "FAILURE"
    assert kwargs["resource_id"] == "sub_huerfana"
    assert kwargs["details"]["stripe_customer_id"] == "cus_sin_enlazar"


def test_webhook_fallo_del_manejador_permite_reintento(client):
    """Reservar antes de procesar no puede convertir un fallo en pérdida.

    La reserva se suelta si el manejador revienta; de lo contrario el reintento
    de Stripe vería «ya procesado» y el evento se perdería para siempre.
    """
    event = {"id": "evt_reintento_1", "object": "event", "type": "customer.subscription.updated", "data": {"object": {"id": "sub_x"}}}
    payload = json.dumps(event).encode()

    with patch.object(billing_webhook, "STRIPE_WEBHOOK_SECRET", _WEBHOOK_SECRET):
        sig_header = _sign_payload(payload, _WEBHOOK_SECRET)
        cabeceras = {"stripe-signature": sig_header}
        with patch.object(
            billing_webhook,
            "_handle_subscription_event",
            new_callable=AsyncMock,
            side_effect=[RuntimeError("la BD parpadeó"), None],
        ) as mock_handle:
            with pytest.raises(RuntimeError):
                client.post("/api/billing/webhook", content=payload, headers=cabeceras)

            r = client.post("/api/billing/webhook", content=payload, headers=cabeceras)

    assert r.status_code == 200, r.text
    assert mock_handle.call_count == 2


# ── BE-09: /quote era el único POST de billing sin freno ──────────────────────


def test_quote_tiene_rate_limit(client):
    """Sin auth (a propósito: calcula un precio público) pero con límite."""
    cuerpo = {"tier": "developer", "seats": 1, "interval": "month"}
    for _ in range(30):
        assert client.post("/api/billing/quote", json=cuerpo).status_code == 200

    r = client.post("/api/billing/quote", json=cuerpo)
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limit_exceeded"
