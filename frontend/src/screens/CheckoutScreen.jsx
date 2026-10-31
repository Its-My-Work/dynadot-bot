/** Checkout: order creation, payment, and live registration status.
 *
 * The flow is a small state machine rather than separate screens, because the
 * user's mental model is one continuous "buy this domain" action:
 *
 *   contact? -> order -> payment link -> poll until the worker finishes
 *
 * Polling (not waiting on a webhook) is deliberate: the TMA has no push
 * channel, and the registration happens in a background worker whose outcome
 * can be REFUND_REQUIRED. The terminal states are explicit so the user is
 * never left staring at a spinner after their money has moved.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import api, { ApiError } from '../lib/api';
import { formatPrice } from '../lib/i18n';
import { haptic, openPaymentUrl } from '../lib/telegram';
import ContactForm from './ContactForm';
import StatusBadge from '../components/StatusBadge';
import { ErrorNote, Spinner } from '../components/ui';

const POLL_MS = 3000;
const PAYMENT_TIMEOUT_S = 15 * 60; // 15 minutes

/** Order statuses after which polling is pointless. */
export const TERMINAL_STATUSES = [
  'completed',
  'failed',
  'refund_required',
  'refunded',
  'cancelled',
];

export function isTerminal(status) {
  return TERMINAL_STATUSES.includes(status);
}

/** The single message that explains "where is my domain?" at any moment. */
export function statusMessage(status, t) {
  switch (status) {
    case 'paid':
    case 'registering':
      return { text: t('pay.registering'), tone: 'accent' };
    case 'completed':
      return { text: t('pay.done'), tone: 'ok' };
    case 'refund_required':
    case 'refunded':
      return { text: t('pay.refund'), tone: 'warn' };
    case 'failed':
      return { text: t('pay.failed'), tone: 'danger' };
    default:
      return { text: t('pay.waiting'), tone: 'muted' };
  }
}

/** Step indicator: 1 → 2 → 3 */
function StepIndicator({ step }) {
  const steps = ['Данные', 'Оплата', 'Готово'];
  return (
    <div className="flex items-center justify-center gap-0">
      {steps.map((label, i) => {
        const n = i + 1;
        const done = n < step;
        const active = n === step;
        return (
          <div key={n} className="flex items-center">
            <div className="flex flex-col items-center gap-0.5">
              <div
                className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold transition-colors ${
                  done
                    ? 'bg-ok text-white'
                    : active
                      ? 'bg-accent text-accent-ink'
                      : 'bg-elevated text-muted'
                }`}
              >
                {done ? '✓' : n}
              </div>
              <span
                className={`text-[10px] font-medium ${active ? 'text-ink' : 'text-muted'}`}
              >
                {label}
              </span>
            </div>
            {i < steps.length - 1 ? (
              <div
                className={`mb-3.5 h-px w-8 transition-colors ${done ? 'bg-ok' : 'bg-line'}`}
              />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** Counts down from PAYMENT_TIMEOUT_S; returns formatted mm:ss string or null when expired. */
function usePaymentTimer(active) {
  const [remaining, setRemaining] = useState(PAYMENT_TIMEOUT_S);

  useEffect(() => {
    if (!active) return;
    setRemaining(PAYMENT_TIMEOUT_S);
    const id = setInterval(() => {
      setRemaining((s) => (s > 0 ? s - 1 : 0));
    }, 1000);
    return () => clearInterval(id);
  }, [active]);

  if (!active) return null;
  const m = Math.floor(remaining / 60);
  const s = remaining % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/** СБП + Мир trust badge row */
function PaymentTrustBadge() {
  return (
    <div className="flex items-center justify-center gap-3 rounded-xl border border-line/60 bg-elevated/40 px-4 py-2.5">
      {/* СБП logo — stylized */}
      <div className="flex items-center gap-1">
        <svg width="26" height="16" viewBox="0 0 26 16" fill="none" aria-label="СБП">
          <rect width="26" height="16" rx="3" fill="#1C3F6E" />
          <path d="M5 8.5 L9 4 L13 8.5 L9 13Z" fill="#00B4D8" />
          <path d="M11 8.5 L15 4 L19 8.5 L15 13Z" fill="#00CBA8" />
          <path d="M17 8.5 L21 5 L21 12Z" fill="#00E5FF" />
        </svg>
        <span className="text-[10px] font-semibold text-muted">СБП</span>
      </div>
      <div className="h-4 w-px bg-line" />
      {/* Мир logo */}
      <div className="flex items-center gap-1">
        <svg width="28" height="16" viewBox="0 0 28 16" fill="none" aria-label="Мир">
          <rect width="28" height="16" rx="3" fill="#4DB45E" />
          <text x="4" y="11.5" fontSize="8" fontWeight="700" fill="white" fontFamily="Arial">МИР</text>
        </svg>
        <span className="text-[10px] font-semibold text-muted">Мир</span>
      </div>
      <div className="h-4 w-px bg-line" />
      <div className="flex items-center gap-1 text-[10px] text-muted">
        <span className="text-ok">🔒</span>
        <span>Безопасная оплата</span>
      </div>
    </div>
  );
}

export default function CheckoutScreen({ t, telegramId, candidate, onDone }) {
  const [years, setYears] = useState(1);
  const [needsContact, setNeedsContact] = useState(false);
  const [contactChecked, setContactChecked] = useState(false);
  const [order, setOrder] = useState(null);
  const [payment, setPayment] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  const paymentActive =
    order && !isTerminal(order.status) && order.status !== 'registering' && order.status !== 'paid';
  const timerDisplay = usePaymentTimer(paymentActive);

  // Does this customer already have saved owner details? Decided once, before
  // anything is charged, so the form never interrupts a payment.
  useEffect(() => {
    let cancelled = false;
    api
      .contacts(telegramId)
      .then((list) => {
        if (cancelled) return;
        setNeedsContact(!Array.isArray(list) || list.length === 0);
        setContactChecked(true);
      })
      .catch(() => {
        if (cancelled) return;
        // Assume a contact is needed: registering with no owner data fails
        // later, which is a far worse place to discover the problem.
        setNeedsContact(true);
        setContactChecked(true);
      });
    return () => {
      cancelled = true;
    };
  }, [telegramId]);

  const mapError = useCallback(
    (cause) => {
      if (cause instanceof ApiError) {
        if (cause.isConflict) return t('buy.taken_now');
        if (cause.isUnavailable) return t('buy.registrar_down');
        if (cause.status === 0) return t('common.network');
      }
      return t('common.error');
    },
    [t],
  );

  async function saveContact(values) {
    setBusy(true);
    setError(null);
    try {
      await api.createContact(telegramId, { ...values, is_default: true });
      setNeedsContact(false);
    } catch (cause) {
      setError(mapError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function startPayment() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createOrder(telegramId, {
        domain_name: candidate.domain,
        years,
      });
      setOrder(created);

      const session = await api.checkout(created.id);
      setPayment(session);
      haptic('success');
      if (session?.payment_url) openPaymentUrl(session.payment_url);
    } catch (cause) {
      haptic('error');
      setError(mapError(cause));
    } finally {
      setBusy(false);
    }
  }

  // Poll the order until the worker reaches a terminal state.
  useEffect(() => {
    if (!order || isTerminal(order.status)) return undefined;

    async function tick() {
      try {
        const fresh = await api.order(order.id);
        setOrder((current) =>
          current && fresh.status !== current.status ? fresh : current ?? fresh,
        );
        if (isTerminal(fresh.status)) {
          haptic(fresh.status === 'completed' ? 'success' : 'warning');
        }
      } catch {
        // Transient poll failures are ignored; the next tick retries.
      }
    }

    pollRef.current = setInterval(tick, POLL_MS);
    return () => clearInterval(pollRef.current);
  }, [order]);

  async function confirmPaid() {
    if (!payment?.payment_id) return;
    setBusy(true);
    try {
      // Ask the backend to poll the gateway: the webhook may have been lost,
      // and this is the user's "I already paid" escape hatch.
      await api.refreshPayment(payment.payment_id);
      const fresh = await api.order(order.id);
      setOrder(fresh);
    } catch (cause) {
      setError(mapError(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!contactChecked) {
    return (
      <div className="flex justify-center py-16 text-muted">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }

  // Step 1: contact form
  if (needsContact) {
    return (
      <div className="space-y-4">
        <StepIndicator step={1} />
        <Header domain={candidate.domain} price={candidate.price_rub} t={t} onBack={onDone} />
        <h2 className="text-sm font-medium">{t('buy.owner')}</h2>
        {error ? <ErrorNote message={error} /> : null}
        <ContactForm t={t} onSubmit={saveContact} onCancel={onDone} submitting={busy} />
      </div>
    );
  }

  // Step 3: order tracking
  if (order) {
    const message = statusMessage(order.status, t);
    const isDone = order.status === 'completed';
    const step = isDone || isTerminal(order.status) ? 3 : 2;
    return (
      <div className="space-y-4">
        <StepIndicator step={step} />
        <Header domain={order.domain_name} price={order.price_rub} t={t} onBack={onDone} />

        <div className="card space-y-3 p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted">{t('buy.title')}</span>
            <StatusBadge status={order.status} label={t(`status.${order.status}`)} />
          </div>

          {/* Payment countdown timer */}
          {timerDisplay ? (
            <div className="flex items-center justify-between rounded-lg border border-warn/30 bg-warn/10 px-3 py-2">
              <span className="text-xs text-warn">Время на оплату</span>
              <span className="font-mono text-sm font-bold tabular-nums text-warn">
                {timerDisplay}
              </span>
            </div>
          ) : null}

          <p
            className={`text-sm ${
              message.tone === 'ok'
                ? 'text-ok'
                : message.tone === 'warn'
                  ? 'text-warn'
                  : message.tone === 'danger'
                    ? 'text-danger'
                    : 'text-ink'
            }`}
          >
            {message.text}
          </p>

          {!isTerminal(order.status) &&
          order.status !== 'paid' &&
          order.status !== 'registering' ? (
            <div className="grid grid-cols-2 gap-2">
              {payment?.payment_url ? (
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => openPaymentUrl(payment.payment_url)}
                >
                  {t('pay.open')}
                </button>
              ) : null}
              <button
                type="button"
                className="btn-ghost"
                onClick={confirmPaid}
                disabled={busy}
              >
                {busy ? <Spinner /> : null}
                {t('pay.check')}
              </button>
            </div>
          ) : null}

          {isTerminal(order.status) ? (
            <button type="button" className="btn-ghost w-full" onClick={onDone}>
              {t('common.close')}
            </button>
          ) : null}
        </div>

        {!isTerminal(order.status) ? <PaymentTrustBadge /> : null}

        {error ? <ErrorNote message={error} /> : null}
      </div>
    );
  }

  // Step 2: order form
  return (
    <div className="space-y-4">
      <StepIndicator step={2} />
      <Header
        domain={candidate.domain}
        price={candidate.price_rub * years}
        t={t}
        onBack={onDone}
      />

      <div className="card divide-y divide-line">
        <div className="flex items-center justify-between p-4">
          <span className="text-sm text-muted">{t('buy.years')}</span>
          <div className="flex gap-1.5">
            {[1, 2, 3].map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  haptic('light');
                  setYears(value);
                }}
                className={`badge border px-3 py-1.5 ${
                  years === value
                    ? 'border-accent/50 bg-accent/15 text-accent'
                    : 'border-line bg-surface text-muted'
                }`}
              >
                {t('buy.years_value', { years: value })}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between p-4">
          <span className="text-sm text-muted">{t('buy.owner')}</span>
          <span className="text-sm text-ok">{t('buy.owner_saved')}</span>
        </div>

        <div className="flex items-center justify-between p-4">
          <span className="text-sm text-muted">{t('buy.total')}</span>
          <span className="text-lg font-semibold">
            {formatPrice(candidate.price_rub * years, t.language)} ₽
          </span>
        </div>
      </div>

      <PaymentTrustBadge />

      {error ? <ErrorNote message={error} /> : null}

      <button
        type="button"
        className="btn-primary w-full py-3 text-base"
        onClick={startPayment}
        disabled={busy}
      >
        {busy ? <Spinner /> : null}
        {busy
          ? t('buy.creating')
          : t('buy.pay', {
              price: formatPrice(candidate.price_rub * years, t.language),
            })}
      </button>

      <p className="text-center text-[11px] text-muted">
        Оплата через СБП или карту Мир · Без комиссий
      </p>
    </div>
  );
}

function Header({ domain, price, t, onBack }) {
  return (
    <div className="space-y-2">
      {onBack ? (
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-1 text-xs font-medium text-muted transition-colors hover:text-ink cursor-pointer py-1"
        >
          <span aria-hidden="true">←</span>
          <span>{t('common.back')}</span>
        </button>
      ) : null}
      <div className="space-y-0.5">
        <h1 className="text-xl font-semibold tracking-tight">{domain}</h1>
        {price !== undefined ? (
          <p className="text-sm text-muted">
            {formatPrice(price, t.language)} ₽
          </p>
        ) : null}
      </div>
    </div>
  );
}
