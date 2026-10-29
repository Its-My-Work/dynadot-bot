/** Order history.
 *
 * Kept read-only apart from one action: resuming payment on an unpaid order.
 * An abandoned checkout is the most common recoverable state, and making the
 * user search for the domain again is how that sale is lost.
 */
import { useCallback, useEffect, useState } from 'react';

import api from '../lib/api';
import { formatDate, formatPrice } from '../lib/i18n';
import { openPaymentUrl } from '../lib/telegram';
import StatusBadge from '../components/StatusBadge';
import { EmptyState, ErrorNote, SkeletonList, Spinner } from '../components/ui';

const RESUMABLE = ['draft', 'awaiting_payment'];

export default function OrdersScreen({ t, telegramId }) {
  const [orders, setOrders] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const list = await api.orders(telegramId);
      setOrders(Array.isArray(list) ? list : []);
    } catch {
      setError(t('common.network'));
      setOrders(null);
    }
  }, [telegramId, t]);

  useEffect(() => {
    load();
  }, [load]);

  async function resume(order) {
    setBusyId(order.id);
    try {
      const session = await api.checkout(order.id);
      if (session?.payment_url) openPaymentUrl(session.payment_url);
    } catch {
      setError(t('common.error'));
    } finally {
      setBusyId(null);
    }
  }

  if (error) {
    return <ErrorNote message={error} onRetry={load} retryLabel={t('common.retry')} />;
  }

  if (orders === null) return <SkeletonList rows={3} />;

  if (orders.length === 0) {
    return <EmptyState icon="▤" title={t('orders.empty')} />;
  }

  return (
    <ul className="space-y-2">
      {orders.map((order) => (
        <li key={order.id} className="card p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate font-medium">{order.domain_name}</p>
              <p className="text-xs text-muted">
                {formatDate(order.created_at, t.language)} ·{' '}
                {formatPrice(order.price_rub, t.language)} ₽
              </p>
            </div>
            <StatusBadge status={order.status} label={t(`status.${order.status}`)} />
          </div>

          {order.error_message && order.status === 'refund_required' ? (
            <p className="mt-2 text-xs text-warn">{t('pay.refund')}</p>
          ) : null}

          {RESUMABLE.includes(order.status) ? (
            <button
              type="button"
              className="btn-primary mt-3 w-full py-2 text-sm"
              onClick={() => resume(order)}
              disabled={busyId === order.id}
            >
              {busyId === order.id ? <Spinner /> : null}
              {t('orders.retry_pay')}
            </button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
