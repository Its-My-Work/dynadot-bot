/** Admin panel: a dense, keyboard-friendly operator console.
 *
 * Priorities, in order:
 *
 * 1. **Refunds first.** Money we owe customers is the only truly urgent thing
 *    in this system, so it is the landing view and is counted in the header.
 * 2. **Dead-lettered tasks are one click from a retry.** A registrar outage
 *    should not require a database console to recover from.
 * 3. **Density over decoration.** Linear/Vercel register: one accent, thin
 *    separators, tabular numbers, no card shadows competing for attention.
 */
import { useCallback, useEffect, useState } from 'react';

import api from './lib/api';
import StatusBadge from './components/StatusBadge';
import { AppBar, Brand, EmptyState, ErrorNote, SkeletonList, Spinner, ThemePicker } from './components/ui';
import useTheme from './hooks/useTheme';

const VIEWS = [
  { id: 'overview', label: 'Overview' },
  { id: 'refunds', label: 'Refunds' },
  { id: 'orders', label: 'Orders' },
  { id: 'tasks', label: 'Queue' },
  { id: 'pricing', label: 'Pricing' },
  { id: 'logs', label: 'API log' },
];

const money = (value) =>
  new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(
    Number(value || 0),
  );

const when = (value) =>
  value
    ? new Intl.DateTimeFormat('en-GB', {
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      }).format(new Date(value))
    : '—';

export default function AdminApp() {
  const [view, setView] = useState('overview');
  const [stats, setStats] = useState(null);
  // No telegramId in the operator console: the theme is a per-browser
  // preference here, so localStorage alone is the right scope.
  const { theme, selectTheme } = useTheme({ persist: false });

  const loadStats = useCallback(() => {
    api
      .adminStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  useEffect(() => {
    loadStats();
    // Operators leave this open; a slow refresh keeps the refund count honest
    // without hammering the API.
    const timer = setInterval(loadStats, 30000);
    return () => clearInterval(timer);
  }, [loadStats]);

  return (
    <div className="min-h-full">
      <AppBar>
        <div className="mx-auto flex h-full max-w-6xl items-center gap-6 px-6">
          <Brand subtitle="Admin" />
          <nav className="flex gap-1">
            {VIEWS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setView(item.id)}
                className={`rounded-md px-2.5 py-1 text-sm transition-colors ${
                  view === item.id
                    ? 'bg-elevated text-ink'
                    : 'text-muted hover:text-ink'
                }`}
              >
                {item.label}
                {item.id === 'refunds' && stats?.refunds_pending ? (
                  <span className="ml-2 rounded-full bg-warn/20 px-1.5 text-xs text-warn">
                    {stats.refunds_pending}
                  </span>
                ) : null}
              </button>
            ))}
          </nav>
          <div className="ml-auto">
            <ThemePicker theme={theme} onSelect={selectTheme} />
          </div>
        </div>
      </AppBar>

      <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
        {view === 'overview' ? <Overview stats={stats} /> : null}
        {view === 'refunds' ? <Refunds onChange={loadStats} /> : null}
        {view === 'orders' ? <Orders /> : null}
        {view === 'tasks' ? <Tasks onChange={loadStats} /> : null}
        {view === 'pricing' ? <Pricing /> : null}
        {view === 'logs' ? <ApiLogs /> : null}
      </main>
    </div>
  );
}

// ------------------------------------------------------------------ overview

function Overview({ stats }) {
  if (!stats) return <SkeletonList rows={2} />;

  const cards = [
    { label: 'Revenue', value: `${money(stats.revenue_rub)} ₽`, tone: 'ok' },
    { label: 'Completed', value: stats.orders_completed },
    { label: 'Awaiting payment', value: stats.orders_awaiting_payment },
    {
      label: 'Refunds pending',
      value: stats.refunds_pending,
      tone: stats.refunds_pending > 0 ? 'warn' : undefined,
    },
    { label: 'Active domains', value: stats.domains_active },
    { label: 'Users', value: stats.users },
    { label: 'Queued tasks', value: stats.tasks_queued },
    {
      label: 'Dead letter',
      value: stats.tasks_dead_letter,
      tone: stats.tasks_dead_letter > 0 ? 'danger' : undefined,
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {cards.map((card) => (
        <div key={card.label} className="card p-4">
          <p className="text-xs text-muted">{card.label}</p>
          <p
            className={`mt-1 text-2xl font-semibold tabular-nums ${
              card.tone === 'ok'
                ? 'text-ok'
                : card.tone === 'warn'
                  ? 'text-warn'
                  : card.tone === 'danger'
                    ? 'text-danger'
                    : 'text-ink'
            }`}
          >
            {card.value}
          </p>
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------- refunds

function Refunds({ onChange }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(() => {
    setError(null);
    api
      .adminRefunds()
      .then((list) => setRows(Array.isArray(list) ? list : []))
      .catch(() => {
        setError('Could not load the refund queue.');
        setRows(null);
      });
  }, []);

  useEffect(load, [load]);

  async function resolve(order) {
    // Refunds are paid out in the gateway console; this only closes our ledger.
    if (!window.confirm(`Mark ${order.domain_name} as refunded?`)) return;
    setBusyId(order.id);
    try {
      await api.resolveRefund(order.id, 'resolved from admin panel');
      load();
      onChange?.();
    } catch {
      setError('Could not resolve the refund.');
    } finally {
      setBusyId(null);
    }
  }

  if (error) return <ErrorNote message={error} onRetry={load} />;
  if (rows === null) return <SkeletonList rows={3} />;
  if (rows.length === 0) {
    return (
      <EmptyState icon="✓" title="No pending refunds" hint="Nothing is owed." />
    );
  }

  return (
    <Table
      head={['Domain', 'Amount', 'Reason', 'Created', '']}
      rows={rows.map((order) => [
        <span key="d" className="font-medium">
          {order.domain_name}
        </span>,
        <span key="a" className="tabular-nums">
          {money(order.price_rub)} ₽
        </span>,
        <span key="r" className="text-muted">
          {order.error_message || order.error_code || '—'}
        </span>,
        <span key="c" className="text-muted">
          {when(order.created_at)}
        </span>,
        <button
          key="b"
          type="button"
          className="btn-ghost px-3 py-1.5 text-xs"
          onClick={() => resolve(order)}
          disabled={busyId === order.id}
        >
          {busyId === order.id ? <Spinner className="h-3 w-3" /> : null}
          Mark refunded
        </button>,
      ])}
    />
  );
}

// -------------------------------------------------------------------- orders

function Orders() {
  const [rows, setRows] = useState(null);
  const [status, setStatus] = useState('');
  const [error, setError] = useState(null);

  useEffect(() => {
    setRows(null);
    setError(null);
    api
      .adminOrders(status ? { status } : undefined)
      .then((list) => setRows(Array.isArray(list) ? list : []))
      .catch(() => setError('Could not load orders.'));
  }, [status]);

  const statuses = [
    '',
    'draft',
    'awaiting_payment',
    'paid',
    'registering',
    'completed',
    'failed',
    'refund_required',
    'refunded',
    'cancelled',
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {statuses.map((value) => (
          <button
            key={value || 'all'}
            type="button"
            onClick={() => setStatus(value)}
            className={`badge border px-2.5 py-1 ${
              status === value
                ? 'border-accent/50 bg-accent/15 text-accent'
                : 'border-line bg-surface text-muted'
            }`}
          >
            {value || 'all'}
          </button>
        ))}
      </div>

      {error ? <ErrorNote message={error} /> : null}
      {rows === null && !error ? <SkeletonList rows={4} /> : null}
      {rows?.length === 0 ? <EmptyState icon="∅" title="No orders" /> : null}
      {rows?.length ? (
        <Table
          head={['Domain', 'Status', 'Price', 'Cost', 'Markup', 'Created']}
          rows={rows.map((order) => [
            <span key="d" className="font-medium">
              {order.domain_name}
            </span>,
            <StatusBadge key="s" status={order.status} label={order.status} />,
            <span key="p" className="tabular-nums">
              {money(order.price_rub)} ₽
            </span>,
            <span key="c" className="tabular-nums text-muted">
              ${Number(order.cost_usd || 0).toFixed(2)}
            </span>,
            <span key="m" className="tabular-nums text-muted">
              {Number(order.markup_percent || 0).toFixed(0)}%
            </span>,
            <span key="t" className="text-muted">
              {when(order.created_at)}
            </span>,
          ])}
        />
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------- tasks

function Tasks({ onChange }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(() => {
    setError(null);
    api
      .adminTasks()
      .then((list) => setRows(Array.isArray(list) ? list : []))
      .catch(() => setError('Could not load the task queue.'));
  }, []);

  useEffect(load, [load]);

  async function retry(task) {
    setBusyId(task.id);
    try {
      await api.retryTask(task.id);
      load();
      onChange?.();
    } catch {
      setError('Could not requeue the task.');
    } finally {
      setBusyId(null);
    }
  }

  if (error) return <ErrorNote message={error} onRetry={load} />;
  if (rows === null) return <SkeletonList rows={4} />;
  if (rows.length === 0) {
    return <EmptyState icon="✓" title="Queue is empty" />;
  }

  return (
    <Table
      head={['Operation', 'Status', 'Attempts', 'Error', 'Scheduled', '']}
      rows={rows.map((task) => [
        <span key="o" className="font-mono text-xs">
          {task.operation}
        </span>,
        <StatusBadge key="s" status={task.status} label={task.status} />,
        <span key="a" className="tabular-nums text-muted">
          {task.attempts}/{task.max_attempts}
        </span>,
        <span key="e" className="max-w-xs truncate text-muted" title={task.last_error}>
          {task.last_error || '—'}
        </span>,
        <span key="t" className="text-muted">
          {when(task.scheduled_at)}
        </span>,
        ['failed', 'dead_letter', 'succeeded'].includes(task.status) ? (
          <button
            key="b"
            type="button"
            className="btn-ghost px-3 py-1.5 text-xs"
            onClick={() => retry(task)}
            disabled={busyId === task.id}
          >
            {busyId === task.id ? <Spinner className="h-3 w-3" /> : null}
            Retry
          </button>
        ) : (
          <span key="b" />
        ),
      ])}
    />
  );
}

// ------------------------------------------------------------------- pricing

function Pricing() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [savingTld, setSavingTld] = useState(null);

  const load = useCallback(() => {
    setError(null);
    api
      .adminPricing()
      .then((list) => setRows(Array.isArray(list) ? list : []))
      .catch(() => setError('Could not load pricing.'));
  }, []);

  useEffect(load, [load]);

  async function save(tld, patch) {
    setSavingTld(tld);
    try {
      const updated = await api.updatePricing(tld, patch);
      setRows((current) => current.map((r) => (r.tld === tld ? updated : r)));
    } catch {
      setError(`Could not update .${tld}.`);
    } finally {
      setSavingTld(null);
    }
  }

  if (error) return <ErrorNote message={error} onRetry={load} />;
  if (rows === null) return <SkeletonList rows={4} />;
  if (rows.length === 0) {
    return (
      <EmptyState
        icon="◇"
        title="No zones configured"
        hint="Sync TLD prices from Dynadot to populate this table."
      />
    );
  }

  return (
    <>
      <p className="text-sm text-muted">
        Changes apply to new orders only — existing orders keep the price frozen
        at checkout.
      </p>
      <Table
        head={['Zone', 'Cost', 'Markup %', 'Fixed ₽', 'Enabled', '']}
        rows={rows.map((row) => [
          <span key="z" className="font-medium">
            .{row.tld}
          </span>,
          <span key="c" className="tabular-nums text-muted">
            ${Number(row.register_cost_usd || 0).toFixed(2)}
          </span>,
          <EditableNumber
            key="m"
            value={row.markup_percent}
            onSave={(value) => save(row.tld, { markup_percent: value })}
          />,
          <EditableNumber
            key="f"
            value={row.fixed_price_rub}
            onSave={(value) => save(row.tld, { fixed_price_rub: value })}
          />,
          <button
            key="e"
            type="button"
            onClick={() => save(row.tld, { is_enabled: !row.is_enabled })}
            className={`badge ${
              row.is_enabled ? 'bg-ok/15 text-ok' : 'bg-muted/15 text-muted'
            }`}
          >
            {row.is_enabled ? 'on' : 'off'}
          </button>,
          savingTld === row.tld ? <Spinner key="s" className="h-3 w-3" /> : <span key="s" />,
        ])}
      />
    </>
  );
}

function EditableNumber({ value, onSave }) {
  const [draft, setDraft] = useState(value ?? '');

  return (
    <input
      className="w-24 rounded-lg border border-line bg-surface px-2 py-1 text-sm
        tabular-nums text-ink"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        const trimmed = String(draft).trim();
        const next = trimmed === '' ? null : Number(trimmed);
        if (next !== null && Number.isNaN(next)) {
          setDraft(value ?? '');
          return;
        }
        if (next !== (value ?? null)) onSave(next);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur();
      }}
      placeholder="—"
    />
  );
}

// ---------------------------------------------------------------------- logs

function ApiLogs() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setError(null);
    api
      .adminApiLogs({ limit: 100 })
      .then((list) => setRows(Array.isArray(list) ? list : []))
      .catch(() => setError('Could not load API logs.'));
  }, []);

  useEffect(load, [load]);

  if (error) return <ErrorNote message={error} onRetry={load} />;
  if (rows === null) return <SkeletonList rows={5} />;
  if (rows.length === 0) return <EmptyState icon="▤" title="No API calls yet" />;

  return (
    <Table
      head={['Method', 'Endpoint', 'Status', 'HTTP', 'ms', 'Try', 'When']}
      rows={rows.map((log) => [
        <span key="m" className="font-mono text-xs text-muted">
          {log.method}
        </span>,
        <span key="e" className="truncate font-mono text-xs">
          {log.endpoint}
        </span>,
        <StatusBadge
          key="s"
          status={log.status === 'success' ? 'succeeded' : 'failed'}
          label={log.status}
        />,
        <span key="h" className="tabular-nums text-muted">
          {log.http_status ?? '—'}
        </span>,
        <span key="d" className="tabular-nums text-muted">
          {log.duration_ms ? Math.round(log.duration_ms) : '—'}
        </span>,
        <span key="a" className="tabular-nums text-muted">
          {log.attempt}
        </span>,
        <span key="w" className="text-muted">
          {when(log.created_at)}
        </span>,
      ])}
    />
  );
}

// --------------------------------------------------------------------- table

function Table({ head, rows }) {
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line">
            {head.map((label, index) => (
              <th
                key={index}
                className="whitespace-nowrap px-4 py-2.5 text-xs font-medium text-muted"
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((cells, rowIndex) => (
            <tr
              key={rowIndex}
              className="border-b border-line/60 last:border-0 hover:bg-elevated/60"
            >
              {cells.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-4 py-2.5">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
