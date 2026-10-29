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
├── integrations/
│   ├── dynadot/
│   │   ├── signing.py      # HMAC-SHA256 request signing
│   │   ├── queue.py        # Serialising priority queue + rate limiter
│   │   ├── client.py       # Typed REST v2 adapter
│   │   └── exceptions.py
│   └── payments/
│       ├── base.py         # PaymentGateway ABC + intent/session/event DTOs
│       ├── platega.py      # Platega adapter (SBP/card/crypto, RU)
│       └── exceptions.py
├── services/
│   └── payments.py         # Checkout + idempotent webhook application
├── bot/                    # aiogram 3.x
│   ├── runner.py           # Bot/Dispatcher factories, polling entrypoint
│   ├── handlers.py         # /start, /help, /lang, /orders, callbacks
│   ├── keyboards.py        # TMA web-app button, language picker, pay button
│   ├── middlewares.py      # User upsert + request-scoped RU/EN translator
│   └── notifications.py    # Outbound payment/order status messages
├── worker/
│   └── __main__.py         # `python -m app.worker` process entrypoint
└── api/
    ├── deps.py             # DB session, language, Dynadot, payment gateway
    └── routes/             # meta, users, contacts, domains, orders,
                            # payments, webhooks, admin
services/
├── payments.py             # Checkout + idempotent webhook application
├── pricing.py              # USD cost -> RUB price, admin overrides
├── registration.py         # Paid order -> registered domain
└── worker.py               # DynadotTask queue + polling worker loop
frontend/                   # React 18 + Tailwind (TMA + admin), Vite
├── index.html              # TMA entry (Telegram WebApp SDK)
├── admin.html              # Admin panel entry
└── src/
    ├── lib/                # api client, telegram bridge, i18n
    ├── screens/            # Search, Checkout, Contact, Domains, Orders
    ├── components/         # StatusBadge, ui primitives
    ├── App.jsx             # TMA shell (tabs + checkout overlay)
    └── AdminApp.jsx        # Operator console
migrations/                 # Alembic (async env)
tests/                      # 279 backend tests (+117 frontend)
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

## Payments (Platega / RU)

`PaymentGateway` (`integrations/payments/base.py`) is the only contract business
logic sees, so a second RU gateway or CryptoBot can be added without touching
services. `PlategaGateway` implements it against `app.platega.io`.

### Auth

Platega uses two plain headers rather than a body signature:
`X-MerchantId` and `X-Secret`. It echoes **those same headers back on its
callbacks**, so webhook verification is a constant-time comparison of the
incoming secret against ours — not an HMAC check.

### Tying a callback to an order

`POST /transaction/process` is sent with `payload = order.id`. Platega returns
that value verbatim in the callback, which is what lets a webhook find its
order without trusting gateway-side bookkeeping. The gateway's `transactionId`
is stored on `Payment.external_id` (unique) and is the idempotency key.

### Status mapping

| Platega | Ours |
|---|---|
| `PENDING`, `PROCESSING`, `DISPUTED` | `PENDING` |
| `CONFIRMED`, `SUCCESS` | `SUCCEEDED` |
| `CANCELED`, `FAILED`, `DECLINED` | `FAILED` |
| `EXPIRED`, `TIMEOUT` | `EXPIRED` |
| `REFUNDED`, `PARTIALLY_REFUNDED`, `CHARGEBACKED` | `REFUNDED` |

Two deliberate choices:

- **`DISPUTED` → `PENDING`**, not failed. The money is not ours yet and Platega
  resends `CONFIRMED` once the check clears; treating it as a failure would
  cancel an order that is about to be paid.
- **An unknown status → `PENDING`.** Guessing success hands out a free domain;
  guessing failure cancels a paid order.

### Webhook rules

`POST /api/v1/webhooks/platega` enforces:

- authenticate **before** parsing — an unauthenticated body never reaches the DB
- verification reads the **raw** body (re-serialising a parsed model changes bytes)
- **2xx for anything we decided about**, including duplicates, illegal
  transitions and unknown transactions. Gateways retry on non-2xx, so a 4xx on a
  deliberately-ignored event means the provider hammers us forever.
- **5xx only for genuinely transient failures**, so the retry is useful

`PaymentService.apply_webhook` is idempotent on `external_id` and guards state
transitions. `CONFIRMED` after `CANCELED` **does** credit the customer — a
cancelled-looking payment can still settle, and the money arrived. The reverse
(a late `CANCELED` after `CONFIRMED`) is rejected so a paid order is never
cancelled. `PaymentService.reconcile` polls the gateway for the dropped-webhook
case, which would otherwise strand a paying customer in `AWAITING_PAYMENT`.

## Telegram bot (aiogram 3.x)

`/start`, `/help`, `/lang`, `/orders` plus inline callbacks. Two run modes share
one dispatcher:

- **polling** — `python -m app.bot`, a separate compose service
- **webhook** — set `TELEGRAM_WEBHOOK_BASE_URL`; the API registers the URL on
  startup and `/api/v1/webhooks/telegram` feeds updates into the same dispatcher

Never both: two pollers on one token fight each other.

`UserContextMiddleware` upserts the Telegram user and injects `user`,
`language` and a bound `t` translator into every handler, on **both** messages
and callback queries. A new user's language is seeded from their Telegram
client locale; an explicit `/lang` choice is then never overwritten.

Two pitfalls encoded in code:

- Telegram rejects non-HTTPS web-app URLs, so `main_menu` drops the TMA button
  rather than letting every `/start` fail with a Bad Request.
- The `/`-prefixed catch-all handler must be registered **last** or it shadows
  every real command. `build_router()` returns a fresh router per dispatcher
  because aiogram refuses to attach one router to two dispatchers.

Outbound notifications go through `safe_send`, which swallows Telegram errors:
a user who blocked the bot must not fail a webhook and trigger a gateway retry
of an already-processed payment.

## Registration worker

The paid-order → registered-domain pipeline. This is the most dangerous code
in the system: by the time it runs, the customer's money has already been
taken.

### Why a durable DB-backed worker

* A registration must survive a restart. The webhook returns 200 the moment
  payment is confirmed; if the process died a second later, a paid order would
  be lost with no record of what to do.
* Dynadot serialises requests, so registration cannot simply be fired off
  concurrently from a request handler.
* Retries need state. Attempt counts, backoff and a dead-letter bucket live on
  `DynadotTask` so the admin panel can show and requeue them.

No external broker: PostgreSQL already provides the atomic claim this needs,
and one fewer moving part beats millisecond latency on an operation whose
registrar call takes seconds.

### Flow

```
webhook CONFIRMED ─> PaymentService ─> order.status = PAID
                                    └─> enqueue_registration()  (before the ack)
worker tick ─> reclaim stale ─> adopt orphans ─> claim task
            └─> ensure_available() ─> register() ─> Domain row + notify
```

### Invariants

1. **Claim before calling.** `claim()` runs a conditional
   `UPDATE ... WHERE status = PAID` and commits before any registrar call.
   Whichever worker's statement matches the row owns the registration; a second
   worker matches zero rows and backs off. An interrupted `REGISTERING` claim is
   resumable so a restart never strands a paid order.
2. **Reuse the request id.** `Order.dynadot_request_id` is generated once and
   persisted, and `DynadotTask.request_id` mirrors it. Dynadot treats
   `X-Request-Id` as the idempotency key, so a retry cannot double-register.
3. **Taken domain means refund, never failure.** A name can be registered by
   someone else between checkout and the registrar call. The order becomes
   `REFUND_REQUIRED` — an operator work queue surfaced in the admin panel — not
   `FAILED`. The same applies to our own auth/config errors: our fault, their
   money.
4. **Transport errors retry, business errors do not.** `DynadotTransportError`
   and `DynadotRateLimitError` return the order to `PAID` and reschedule with
   exponential backoff (15s doubling, capped at 600s). A rejected contact or
   drained balance is terminal — retrying only burns rate limit.
5. **Exhausted retries still owe money.** Dead-lettering calls
   `escalate_to_refund()`, so giving up lands in the refund queue rather than a
   silent `FAILED`.

### Safety nets

* **Orphan sweep.** Every tick queues `PAID` orders that have no task, so a
  crash between confirming payment and enqueueing is survivable.
* **Stale reclaim.** A task `RUNNING` for more than 15 minutes lost its worker
  (SIGKILL, OOM) and is returned to the queue.
* **Notification isolation.** A blocked bot cannot undo a completed
  registration; notification failures are logged and swallowed.

### Deployment

One worker process, always: `python -m app.worker` as its own compose service
with `REGISTRATION_WORKER_ENABLED=false` on the API. Two workers would contend
for the same tasks and waste Dynadot's single request slot. For
single-container and local setups, flip `REGISTRATION_WORKER_ENABLED=true` and
the API runs it in-process instead.

## Pricing

`PricingService` is the single source of truth, because three callers need
identical numbers (search grid, checkout, admin panel) and a divergence between
them is a billing bug. Resolution order per zone:

1. `TldPricing.fixed_price_rub` — hard override, wins over everything
2. `TldPricing.markup_percent` — per-zone markup
3. `Settings.default_markup_percent` — global fallback

The resolved breakdown is frozen onto the order at creation, so a later markup
change never alters what a customer already agreed to pay.

## Frontend (React 18 + Tailwind)

One Vite build, two entry points: `index.html` (TMA, loads Telegram's WebApp
SDK) and `admin.html` (operator console). Sharing a bundle keeps the API client
and design tokens in sync between them. FastAPI serves `frontend/dist` from
`/` when it exists, mounted after the API router so it cannot shadow routes.

### TMA

Three tabs (search, domains, orders) plus a checkout overlay. Deliberately no
router: the app is three lists and one flow, and a router would add history
states that fight Telegram's own back button — which drives the overlay
directly instead.

* **Search** is one input, debounced 450ms and abortable. Each search is a
  serialised registrar call, so per-keystroke firing would queue stale work
  ahead of the user's real query. Taken results stay visible but dimmed;
  filtering them out makes the zone grid look random.
* **Checkout** is a small state machine (`contact? → order → payment link →
  poll`) because the user's mental model is one continuous action. Polling, not
  push: the TMA has no push channel, and the registration outcome can be
  `REFUND_REQUIRED` — which gets its own explicit message rather than a bare
  "failed".
* **Contact form** appears exactly once per customer; the profile is saved
  server-side as the default so repeat purchases need zero input.
* **Nameservers** are edited as one-server-per-line text. Users paste the two
  lines Cloudflare gives them; anything fancier only adds taps.

Everything degrades gracefully when `window.Telegram` is absent so the bundle
runs in a plain browser for development and tests. Telegram's colour scheme is
honoured via CSS variables, so the TMA never fights the surrounding client.

### Admin panel

Dark, dense, one accent — Linear/Vercel register. Priorities in order:
**refunds first** (money owed is the only urgent thing, badged in the nav and
counted in the header), **dead-lettered tasks one click from a retry** (a
registrar outage must not need a database console), then orders, pricing and
the Dynadot API log. Revenue counts only `COMPLETED` orders: billing a
refund-pending order as revenue overstates the books by exactly what we owe
back.

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
# Backend
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload     # docs at /api/v1/docs
python -m app.bot                 # bot via long polling (separate process)
python -m app.worker              # registration worker (separate process)
pytest                            # 279 tests
ruff check .

# Frontend
cd frontend
npm install
npm run dev                       # Vite dev server, proxies /api to :8000
npm test                          # 117 tests
npm run build                     # -> frontend/dist, served by FastAPI at /

# or: docker compose up --build    (api + bot + worker + postgres)
```

## Not yet implemented

Telegram `initData` signature verification and admin RBAC (both surfaces are
currently unauthenticated — do not expose the admin panel publicly), a
scheduled `reconcile` sweep for stale pending payments, automated refund
execution (Platega has no refund API, so `REFUND_REQUIRED` is cleared by hand
in the admin panel), TLD price sync from Dynadot, a DNS record editor, and
expiry-reminder scheduling.
