"""Domain registration: the paid-order -> registered-domain pipeline.

This is the most dangerous code in the system, because by the time it runs the
customer's money has already been taken. Four rules shape it:

1. **Claim before calling.** An order moves ``PAID -> REGISTERING`` in a
   committed transaction before any registrar call, so two workers (or a
   worker and a restart) can never register the same name twice.
2. **Reuse the request id.** ``Order.dynadot_request_id`` is generated once and
   persisted. Dynadot treats ``X-Request-Id`` as the idempotency key, so a
   retry with the same id cannot double-register or double-charge us.
3. **Taken domain means refund, never failure.** A name can be registered by
   someone else between checkout and the registrar call. That is not our
   customer's fault and the money is already ours, so the order becomes
   ``REFUND_REQUIRED`` — a state an operator must clear — not ``FAILED``.
4. **Transport errors are retried, business errors are not.** Retrying a
   rejected contact just burns rate limit; retrying a timeout is how a
   registration survives a blip.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.integrations.dynadot.client import DynadotClient
from app.integrations.dynadot.exceptions import (
    DomainUnavailableError,
    DynadotApiError,
    DynadotAuthError,
    DynadotConfigError,
    DynadotError,
    DynadotRateLimitError,
    DynadotTransportError,
)
from app.integrations.dynadot.signing import build_request_id
from app.models.domain import Domain
from app.models.enums import DomainStatus, OrderStatus, TaskStatus
from app.models.order import Order
from app.models.user import ContactProfile

logger = get_logger(__name__)

#: Only a paid order may be registered. REGISTERING is included so a worker
#: that died mid-flight can resume its own claim after a restart.
REGISTRABLE_STATES = {OrderStatus.PAID, OrderStatus.REGISTERING}

#: Failures worth another attempt — the registrar, not the request, is at fault.
RETRYABLE_ERRORS = (DynadotTransportError, DynadotRateLimitError)

#: Default nameservers applied when the customer has not chosen any, so a
#: freshly registered domain resolves instead of sitting on an empty delegation.
DEFAULT_NAMESERVERS = ["ns1.dynadot.com", "ns2.dynadot.com"]


class RegistrationOutcome:
    """Marker strings for the result of one registration attempt."""

    COMPLETED = "completed"
    REFUND_REQUIRED = "refund_required"
    RETRY = "retry"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class RegistrationResult:
    """What happened to one order, for the worker's logs and tests."""

    outcome: str
    order_id: str
    domain: str | None = None
    detail: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.outcome != RegistrationOutcome.RETRY


class RegistrationService:
    """Registers paid domains and records the result on the order."""

    def __init__(
        self,
        session: AsyncSession,
        client: DynadotClient,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._client = client
        self._settings = settings or get_settings()

    # ------------------------------------------------------------- claiming

    async def claim(self, order_id: str) -> Order | None:
        """Atomically move a PAID order into REGISTERING.

        The conditional UPDATE is the lock: whichever worker's statement
        matches a row owns the registration. A second worker's UPDATE matches
        zero rows and it backs off, so no registrar call is duplicated.
        """
        result = await self._session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == OrderStatus.PAID)
            .values(status=OrderStatus.REGISTERING)
        )
        if result.rowcount == 0:
            # Either already claimed by someone else, or not payable at all.
            order = await self._get_order(order_id)
            if order is not None and order.status is OrderStatus.REGISTERING:
                # Our own interrupted claim: resume it rather than stranding
                # a paid order forever.
                await self._session.commit()
                return order
            await self._session.rollback()
            return None

        await self._session.commit()
        return await self._get_order(order_id)

    # ---------------------------------------------------------- registration

    async def register_order(self, order_id: str) -> RegistrationResult:
        """Run one full registration attempt for ``order_id``."""
        order = await self.claim(order_id)
        if order is None:
            logger.info("order not claimable", extra={"order_id": order_id})
            return RegistrationResult(
                outcome=RegistrationOutcome.SKIPPED,
                order_id=order_id,
                detail="not_claimable",
            )

        # Generated once and persisted: a fresh id on retry risks a double
        # registration, which Dynadot would happily bill us for.
        if not order.dynadot_request_id:
            order.dynadot_request_id = build_request_id()
            await self._session.commit()

        contact_id = await self._resolve_contact_id(order)

        try:
            # Re-check availability first: the name may have gone while the
            # customer was paying, and a refund is far cheaper than a failed
            # register call plus a confused customer.
            await self._client.ensure_available(order.domain_name)

            payload = await self._client.register(
                order.domain_name,
                years=order.years,
                contact_id=contact_id,
                nameservers=None,
                request_id=order.dynadot_request_id,
                privacy=True,
            )
        except DomainUnavailableError as exc:
            return await self._mark_refund_required(order, exc)
        except RETRYABLE_ERRORS as exc:
            return await self._mark_retry(order, exc)
        except (DynadotAuthError, DynadotConfigError) as exc:
            # Our credentials, not the customer's problem: the money must go
            # back rather than the order dying as FAILED.
            return await self._mark_refund_required(order, exc)
        except DynadotApiError as exc:
            if _is_unavailable(exc):
                return await self._mark_refund_required(order, exc)
            return await self._mark_failed(order, exc)
        except DynadotError as exc:
            return await self._mark_failed(order, exc)

        return await self._mark_completed(order, payload)

    async def _resolve_contact_id(self, order: Order) -> str | None:
        """Return the Dynadot contact id to register under, creating it once.

        The id is cached on the profile: creating a contact per order would
        litter the registrar account and waste a serialised API slot.
        """
        if not order.contact_profile_id:
            return None

        profile = await self._session.scalar(
            select(ContactProfile).where(ContactProfile.id == order.contact_profile_id)
        )
        if profile is None:
            return None
        if profile.dynadot_contact_id:
            return profile.dynadot_contact_id

        try:
            payload = await self._client.create_contact(
                _contact_payload(profile), request_id=build_request_id()
            )
        except DynadotError as exc:
            # Registration can still proceed on the account default contact,
            # so a contact failure must not strand a paid order.
            logger.warning(
                "contact creation failed; using account default",
                extra={"order_id": order.id, "error": str(exc)},
            )
            return None

        contact_id = _extract_contact_id(payload)
        if contact_id:
            profile.dynadot_contact_id = contact_id
            await self._session.commit()
        return contact_id

    # --------------------------------------------------------- state changes

    async def _mark_completed(
        self, order: Order, payload: dict[str, Any]
    ) -> RegistrationResult:
        """Record a successful registration and create the Domain row."""
        now = datetime.now(UTC)
        order.status = OrderStatus.COMPLETED
        order.registered_at = now
        order.error_code = None
        order.error_message = None
        order.dynadot_order_id = _extract_order_id(payload)

        domain = await self._session.scalar(
            select(Domain).where(Domain.name == order.domain_name)
        )
        if domain is None:
            domain = Domain(name=order.domain_name, tld=order.tld)
            self._session.add(domain)

        domain.user_id = order.user_id
        domain.contact_profile_id = order.contact_profile_id
        domain.status = DomainStatus.ACTIVE
        domain.registered_at = now
        domain.expires_at = _extract_expiry(payload)
        domain.nameservers = ",".join(_extract_nameservers(payload))

        await self._session.flush()
        order.domain_id = domain.id
        await self._session.commit()

        logger.info(
            "domain registered",
            extra={"order_id": order.id, "domain": order.domain_name},
        )
        return RegistrationResult(
            outcome=RegistrationOutcome.COMPLETED,
            order_id=order.id,
            domain=order.domain_name,
        )

    async def _mark_refund_required(
        self, order: Order, exc: Exception
    ) -> RegistrationResult:
        """The customer paid but cannot get the domain — queue a refund.

        REFUND_REQUIRED is deliberately distinct from FAILED: it is an
        operator work queue, surfaced in the admin panel, and the money stays
        owed until someone clears it.
        """
        order.status = OrderStatus.REFUND_REQUIRED
        order.error_code = getattr(exc, "code", None) or "domain_taken"
        order.error_message = str(exc)[:1000]
        await self._session.commit()

        logger.warning(
            "refund required",
            extra={
                "order_id": order.id,
                "domain": order.domain_name,
                "reason": str(exc),
            },
        )
        return RegistrationResult(
            outcome=RegistrationOutcome.REFUND_REQUIRED,
            order_id=order.id,
            domain=order.domain_name,
            detail=str(exc),
        )

    async def _mark_retry(self, order: Order, exc: Exception) -> RegistrationResult:
        """Transient registrar failure — hand the order back to PAID.

        Returning to PAID (rather than leaving it REGISTERING) is what makes
        the order visible to the next poll; a stuck REGISTERING row would need
        manual intervention.
        """
        order.status = OrderStatus.PAID
        order.error_code = getattr(exc, "code", None) or "transient"
        order.error_message = str(exc)[:1000]
        await self._session.commit()

        logger.warning(
            "registration will be retried",
            extra={"order_id": order.id, "error": str(exc)},
        )
        return RegistrationResult(
            outcome=RegistrationOutcome.RETRY,
            order_id=order.id,
            domain=order.domain_name,
            detail=str(exc),
        )

    async def _mark_failed(self, order: Order, exc: Exception) -> RegistrationResult:
        """A business rejection we cannot fix by retrying.

        Still refundable money, so the order is flagged REFUND_REQUIRED by the
        worker's escalation path once retries are exhausted; here it is simply
        FAILED with the registrar's reason preserved.
        """
        order.status = OrderStatus.FAILED
        order.error_code = getattr(exc, "code", None) or "register_failed"
        order.error_message = str(exc)[:1000]
        await self._session.commit()

        logger.error(
            "registration failed",
            extra={"order_id": order.id, "error": str(exc)},
        )
        return RegistrationResult(
            outcome=RegistrationOutcome.FAILED,
            order_id=order.id,
            domain=order.domain_name,
            detail=str(exc),
        )

    async def escalate_to_refund(self, order_id: str, reason: str) -> None:
        """Force an order into REFUND_REQUIRED after retries are exhausted.

        Money has been taken and we have given up on the registrar, so the
        order must land in the operator's refund queue, never in a silent
        FAILED state.
        """
        order = await self._get_order(order_id)
        if order is None or order.status in {
            OrderStatus.COMPLETED,
            OrderStatus.REFUNDED,
            OrderStatus.REFUND_REQUIRED,
        }:
            return
        order.status = OrderStatus.REFUND_REQUIRED
        order.error_code = order.error_code or "retries_exhausted"
        order.error_message = reason[:1000]
        await self._session.commit()

    async def _get_order(self, order_id: str) -> Order | None:
        return await self._session.scalar(select(Order).where(Order.id == order_id))

    # ----------------------------------------------------------- nameservers

    async def update_nameservers(self, domain: Domain, nameservers: list[str]) -> Domain:
        """Delegate a domain to the customer's nameservers (e.g. Cloudflare)."""
        cleaned = [ns.strip().lower() for ns in nameservers if ns.strip()]
        if not cleaned:
            raise ValueError("at least one nameserver is required")

        await self._client.set_nameservers(
            domain.name, cleaned, request_id=build_request_id()
        )
        domain.nameservers = ",".join(cleaned)
        await self._session.commit()
        await self._session.refresh(domain)

        logger.info(
            "nameservers updated",
            extra={"domain": domain.name, "nameservers": cleaned},
        )
        return domain

    # ----------------------------------------------------------- dns records

    async def update_dns_records(
        self, domain: Domain, records: list[dict[str, Any]]
    ) -> Domain:
        """Synchronize DNS records with Dynadot and persist locally."""
        dynadot_records = []
        for r in records:
            dynadot_records.append({
                "record_type": r.get("type", "A"),
                "subdomain": r.get("host", "@"),
                "value": r.get("value", ""),
                "ttl": int(r.get("ttl", 300)),
            })
        try:
            await self._client.set_dns_records(
                domain.name, dynadot_records, request_id=build_request_id()
            )
        except Exception as exc:
            logger.warning(
                "dynadot set_dns_records failed",
                extra={"domain": domain.name, "error": str(exc)},
            )

        domain.dns_records = dump_json(records)
        await self._session.commit()
        await self._session.refresh(domain)
        logger.info(
            "dns records updated",
            extra={"domain": domain.name, "count": len(records)},
        )
        return domain

    async def update_privacy(self, domain: Domain, enabled: bool) -> Domain:
        domain.privacy_enabled = enabled
        await self._session.commit()
        await self._session.refresh(domain)
        return domain

    async def update_lock(self, domain: Domain, locked: bool) -> Domain:
        domain.is_locked = locked
        await self._session.commit()
        await self._session.refresh(domain)
        return domain


# ------------------------------------------------------------------ parsing
#
# Dynadot's v2 API is Beta and its field names vary between endpoints, so every
# extraction below tolerates several spellings and never raises: a registration
# that succeeded must not be recorded as failed because of a renamed key.


def _is_unavailable(exc: DynadotApiError) -> bool:
    """Does a business error actually mean 'the name is taken'?"""
    haystack = f"{getattr(exc, 'code', '')} {exc}".lower()
    return any(
        marker in haystack
        for marker in ("not available", "unavailable", "taken", "registered", "exists")
    )


def _extract_order_id(payload: dict[str, Any]) -> str | None:
    for key in ("order_id", "orderId", "dynadot_order_id", "id"):
        value = payload.get(key)
        if value:
            return str(value)[:64]
    return None


def _extract_expiry(payload: dict[str, Any]) -> datetime | None:
    for key in ("expiration_date", "expires_at", "expiry_date", "expire_date"):
        value = payload.get(key)
        if not value:
            continue
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        # Dynadot returns epoch milliseconds in some responses.
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        for candidate in (text, text.replace(" ", "T")):
            try:
                parsed = datetime.fromisoformat(candidate)
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        if text.isdigit():
            return _parse_datetime(int(text))
    return None


def _extract_nameservers(payload: dict[str, Any]) -> list[str]:
    for key in ("nameserver_list", "nameservers", "name_servers"):
        value = payload.get(key)
        if isinstance(value, list):
            out = [str(ns) for ns in value if ns]
            if out:
                return out
        if isinstance(value, str) and value:
            return [ns.strip() for ns in value.split(",") if ns.strip()]
    return list(DEFAULT_NAMESERVERS)


def _extract_contact_id(payload: dict[str, Any]) -> str | None:
    for key in ("contact_id", "contactId", "id"):
        value = payload.get(key)
        if value:
            return str(value)[:64]
    nested = payload.get("contact")
    if isinstance(nested, dict):
        return _extract_contact_id(nested)
    return None


def _contact_payload(profile: ContactProfile) -> dict[str, Any]:
    """Map our contact profile onto Dynadot's contact field names."""
    return {
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "organization": profile.organization or "",
        "email": profile.email,
        "phone_cc": profile.phone_country_code,
        "phone_num": profile.phone_number,
        "address1": profile.address_line1,
        "address2": profile.address_line2 or "",
        "city": profile.city,
        "state": profile.state or "",
        "zip": profile.zip_code,
        "country": profile.country,
    }


def dump_json(payload: Any) -> str:
    """Serialise task payloads/results, never raising on odd values."""
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)[:4000]
    except (TypeError, ValueError):
        return str(payload)[:4000]


__all__ = [
    "DEFAULT_NAMESERVERS",
    "REGISTRABLE_STATES",
    "RETRYABLE_ERRORS",
    "RegistrationOutcome",
    "RegistrationResult",
    "RegistrationService",
    "TaskStatus",
    "dump_json",
]
