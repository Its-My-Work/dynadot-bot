# Backend Architecture

Scaffold for the FastAPI backend. Covers layout, the Dynadot integration
constraints and the i18n layer.

## Layout

```
app/
├── main.py                 # App factory, lifespan, exception handlers
├── schemas.py              # Pydantic request/response models
├── core/
│   ├── config.py           # Env-driven settings (pydantic-settings)
│   └── logging.py          # JSON logs in prod, readable locally
├── db/
│   ├── base.py             # DeclarativeBase + timestamp/UUID mixins
│   └── session.py          # Async engine, session factory, get_db dependency
├── models/                 # SQLAlchemy 2.0 ORM (Mapped[...] style)
│   ├── user.py             # User, ContactProfile
│   ├── domain.py           # Domain, TldPricing
│   ├── order.py            # Order, Payment
│   ├── audit.py            # DynadotApiLog, DynadotTask, AppSetting
│   └── enums.py            # Shared enumerations
├── i18n/
│   ├── translator.py       # Catalogue loading, fallbacks, Accept-Language
│   └── locales/{ru,en}.json
├── integrations/dynadot/
│   ├── signing.py          # HMAC-SHA256 request signing
│   ├── queue.py            # Serialising priority queue + rate limiter
│   ├── client.py           # Typed REST v2 adapter
│   └── exceptions.py
└── api/
    ├── deps.py             # DB session, language, Dynadot client
    └── routes/             # meta, users, domains, admin
migrations/                 # Alembic (async env)
tests/                      # 49 tests
```

## Dynadot integration

Built against **RESTful v2** (`/restful/v2/...`). The legacy XML API is
scheduled for retirement. v2 is officially Beta, so `client.py` is an adapter —
business logic never touches raw response shapes.

### Signing

Every request carries three headers. `X-Signature` is an HMAC-SHA256 over a
newline-joined string that **includes the serialised body**, so it must be
computed after the body exists:

```
string_to_sign = f"{api_key}\n{path_and_query}\n{x_request_id}\n{body}"
```

`build_path_with_query()` produces the signed path and the wire path from the
same code, so they cannot drift.

### Idempotency

`X-Request-Id` is part of the signature and acts as Dynadot's idempotency key.
Generate it **once per logical operation**, persist it (`Order.dynadot_request_id`,
`DynadotTask.request_id`) and resend the identical value on every retry — a
fresh id on a registration retry risks registering and charging twice.

### Concurrency: the dominant constraint

Dynadot processes **one request at a time** (Regular tier: 60/min). Everything
therefore goes through `DynadotQueue`:

- single worker (`DYNADOT_MAX_CONCURRENCY=1`), sliding-window rate limiter
- priorities so revenue-critical work never starves behind search:
  `CRITICAL` (register/renew) → `HIGH` (NS/DNS/contacts) → `NORMAL` → `LOW` (search)
- exponential backoff with jitter on 429/5xx, honouring `Retry-After`
- `bulk_search` is preferred over N single searches

### Resale hazard: "taken after payment"

A domain can be registered by someone else between taking the customer's money
and calling `register`. `client.ensure_available()` re-checks immediately before
registering and raises `DomainUnavailableError`; `OrderStatus.REFUND_REQUIRED`
models the resulting state so funds are never silently retained.

### Webhooks to handle

`whois_verification_required` (unverified contact ⇒ `clientHold` ⇒ the
customer's site goes offline) and `account_balance_reminder` (a drained
registrar balance fails every registration *after* the customer has paid).

## Internationalisation (RU/EN)

JSON catalogues per language with flat dotted keys. Resolution order:
requested language → default (`ru`) → the key itself. Missing keys and broken
interpolation are logged, never raised — a bad translation cannot break a request.

Language per request comes from `X-Language` (set by the TMA from the saved
profile preference), else `Accept-Language`, else the default. The preference
persists on `User.language`.

A test asserts the RU and EN catalogues have identical key sets.

## Database

SQLAlchemy 2.0 async with `Mapped[...]` annotations; PostgreSQL via `asyncpg`
in production, SQLite via `aiosqlite` for tests. Nine tables. Alembic uses an
async `env.py` that reads the DSN from settings, with
`render_as_batch` for SQLite ALTER support.

Prices are frozen onto the order at checkout (`cost_usd`, `price_rub`,
`markup_percent`, `fx_rate`) so later markup changes never alter what the
customer already agreed to pay.

## Running

```bash
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload     # docs at /api/v1/docs
pytest                            # 49 tests
# or: docker compose up --build
```

## Not yet implemented

Telegram bot handlers, Platega payment flow, Telegram `initData` auth and admin
RBAC, the `DynadotTask` worker loop that drains persisted tasks, TLD price sync,
and the React TMA/web frontend.
