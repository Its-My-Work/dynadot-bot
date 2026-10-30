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

export default function CheckoutScreen({ t, telegramId, candidate, onDone }) {
  const [years, setYears] = useState(1);
  const [needsContact, setNeedsContact] = useState(false);
  const [contactChecked, setContactChecked] = useState(false);
  const [order, setOrder] = useState(null);
  const [payment, setPayment] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

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

  if (needsContact) {
    return (
      <div className="space-y-4">
        <Header domain={candidate.domain} price={candidate.price_rub} t={t} onBack={onDone} />
        <h2 className="text-sm font-medium">{t('buy.owner')}</h2>
        {error ? <ErrorNote message={error} /> : null}
        <ContactForm t={t} onSubmit={saveContact} onCancel={onDone} submitting={busy} />
      </div>
    );
  }

  if (order) {
    const message = statusMessage(order.status, t);
    return (
      <div className="space-y-4">
        <Header domain={order.domain_name} price={order.price_rub} t={t} onBack={onDone} />

        <div className="card space-y-3 p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm text-muted">{t('buy.title')}</span>
            <StatusBadge status={order.status} label={t(`status.${order.status}`)} />
          </div>

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

          {!isTerminal(order.status) && order.status !== 'paid' &&
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

        {error ? <ErrorNote message={error} /> : null}
      </div>
    );
  }

  return (
    <div className="space-y-4">
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
