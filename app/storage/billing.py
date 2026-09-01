"""Storage for Stripe subscriptions and webhook event idempotency."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.sql import sql
from app.storage.db import IS_PG, open_db
from app.utils import now_iso as _now
from app.utils.generators import generate_id

_ACTIVE_STATUSES_EXCLUDED = ("canceled", "incomplete_expired")


class BillingStorage:
    """Suscripciones y eventos de webhook en la base de datos configurada."""

    async def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/billing:latest_by_username"),
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
                sql("queries/billing:get_by_id"), (subscription_id,)
            )
            if row and row["status"] not in _ACTIVE_STATUSES_EXCLUDED:
                return dict(row)
            return None

    async def get_by_stripe_subscription_id(self, stripe_subscription_id: str) -> Optional[Dict[str, Any]]:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/billing:get_by_stripe_id"),
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
                    sql("queries/billing:update_by_stripe_id"),
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
                    sql("queries/billing:insert_subscription"),
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
                sql("queries/billing:set_cancel_at_period_end"),
                (1 if cancel else 0, _now(), stripe_subscription_id),
            )
            await conn.commit()

    async def ensure_owner_license(self, subscription: Dict[str, Any]) -> None:
        """Ensure the buyer consumes one seat for active paid subscriptions."""
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
                    sql("queries/billing:count_active_assignments"),
                    (subscription_id,),
                )
                or 0
            )

    async def has_active_license(self, username: str) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/billing:has_active_license"),
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
                sql("queries/billing:list_assignments"),
                (subscription_id,),
            )
            user_rows = await conn.fetchall(
                sql("queries/billing:list_users")
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
        now = _now()
        async with open_db() as conn:
            # La capacidad se decide y consume bajo el mismo lock. SQLite
            # reserva al escritor antes de leer; PostgreSQL serializa por la
            # fila de la suscripción para permitir paralelismo entre cuentas.
            async with conn.transaction(immediate=True):
                subscription = await conn.fetchone(
                    sql(
                        "queries/billing:get_by_id_for_update"
                        if IS_PG
                        else "queries/billing:get_by_id"
                    ),
                    (subscription_id,),
                )
                if (
                    not subscription
                    or subscription["status"] in _ACTIVE_STATUSES_EXCLUDED
                ):
                    raise ValueError("subscription_not_active")

                if not await conn.fetchone(
                    sql("queries/billing:user_exists"), (target_username,)
                ):
                    raise ValueError("user_not_found")

                existing = await conn.fetchone(
                    sql("queries/billing:active_assignment_for_user"),
                    (target_username,),
                )
                if existing:
                    if (
                        allow_existing
                        and existing["subscription_id"] == subscription_id
                    ):
                        return dict(existing)
                    raise ValueError("license_already_assigned")

                used = int(
                    await conn.fetchval(
                        sql("queries/billing:count_active_assignments"),
                        (subscription_id,),
                    )
                    or 0
                )
                if used >= int(subscription["seats"] or 0):
                    raise ValueError("no_seats_available")

                await conn.execute(
                    sql(
                        "queries/billing:assign_license_pg"
                        if IS_PG
                        else "queries/billing:assign_license_sqlite"
                    ),
                    (subscription_id, target_username, assigned_by, now),
                )

                row = await conn.fetchone(
                    sql("queries/billing:get_assignment"),
                    (subscription_id, target_username),
                )
                return dict(row)

    async def revoke_license(
        self, *, subscription_id: str, target_username: str
    ) -> bool:
        async with open_db() as conn:
            row = await conn.fetchone(
                sql("queries/billing:assignment_is_active"),
                (subscription_id, target_username),
            )
            if not row:
                return False
            await conn.execute(
                sql("queries/billing:revoke_assignment"),
                (subscription_id, target_username),
            )
            await conn.commit()
            return True

    async def claim_event(
        self, stripe_event_id: str, event_type: str, payload: Dict[str, Any]
    ) -> bool:
        """Reserva el evento antes de procesarlo. False si ya lo tenía otro.

        El INSERT es la cerradura. `stripe_event_id` es PRIMARY KEY, así que de
        dos entregas simultáneas del mismo evento —Stripe avisa por escrito de
        que ocurren— solo una gana la fila y solo esa ejecuta el manejador.
        Antes esto eran un SELECT y un INSERT con el manejador entero en medio,
        y las dos entregas pasaban el SELECT.

        `RETURNING` es lo que distingue haber reservado de haber chocado: en el
        conflicto no devuelve fila. El payload se guarda aquí, al reservar, que
        es el momento en que se sabe con certeza que el evento llegó.
        """
        params = (
            stripe_event_id,
            event_type,
            _now(),
            json.dumps(payload, ensure_ascii=False),
        )
        async with open_db() as conn:
            if IS_PG:
                row = await conn.fetchone(
                    sql("queries/billing:claim_stripe_event_pg"), params
                )
            else:
                row = await conn.fetchone(
                    sql("queries/billing:claim_stripe_event_sqlite"), params
                )
            await conn.commit()
            return row is not None

    async def discard_event(self, stripe_event_id: str) -> None:
        """Suelta la reserva para que el reintento de Stripe sí se procese.

        Sin esto, un fallo transitorio a mitad del manejador dejaría el evento
        marcado como procesado y el reintento se lo tragaría en silencio.
        """
        async with open_db() as conn:
            await conn.execute(
                sql("queries/billing:delete_stripe_event"), (stripe_event_id,)
            )
            await conn.commit()

    async def purge_events(self, dias: int) -> int:
        """Borra los eventos de Stripe más antiguos que `dias`. Devuelve cuántos.

        Era la única tabla del esquema sin ningún `DELETE`: no la barre nadie y
        tampoco la alcanza el borrado RGPD, porque no tiene columna de dueño que
        relacione la fila con una persona. Crecía además con todo lo que Stripe
        mandase, no con lo que se procesa.

        Cuánto se guarda es política y la fija el admin; esto solo la aplica.
        """
        from datetime import datetime, timedelta, timezone

        # `processed_at` se escribe con `now_iso()`, así que el corte se genera
        # igual y la comparación de textos ISO ordena bien.
        corte = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        async with open_db() as conn:
            borradas = await conn.fetchall(
                sql("queries/billing:purge_stripe_events"), (corte,)
            )
            await conn.commit()
        return len(borradas)
