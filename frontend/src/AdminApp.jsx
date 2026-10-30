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

import api, { getAdminToken } from './lib/api';
import { makeTranslator } from './lib/i18n';
import StatusBadge from './components/StatusBadge';
import {
  AppBar,
  Brand,
  EmptyState,
  ErrorNote,
  LangToggle,
  Monogram,
  SkeletonList,
  Spinner,
  ThemePicker,
} from './components/ui';
import useTheme from './hooks/useTheme';

const money = (value) =>
  new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(
    Number(value || 0),
  );

const when = (value, language = 'ru') =>
  value
    ? new Intl.DateTimeFormat(language === 'en' ? 'en-GB' : 'ru-RU', {
        day: '2-digit',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
      }).format(new Date(value))
    : '—';

export default function AdminApp() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => Boolean(getAdminToken()));
  const [view, setView] = useState('overview');
  const [stats, setStats] = useState(null);
  const [language, setLanguage] = useState(() => {
    try {
      return localStorage.getItem('dynadot_admin_lang') || 'ru';
    } catch {
      return 'ru';
    }
  });

  const { theme, selectTheme } = useTheme({ persist: false });
  const t = makeTranslator(language);

  const toggleLang = () => {
    const next = language === 'ru' ? 'en' : 'ru';
    setLanguage(next);
    try {
      localStorage.setItem('dynadot_admin_lang', next);
    } catch {
      // Ignored: storage quota or disabled in sandbox.
    }
  };

  const handleLogout = () => {
    api.adminLogout();
    setIsAuthenticated(false);
  };

  const loadStats = useCallback(() => {
    api
      .adminStats()
      .then((res) => {
        setStats(res);
        setIsAuthenticated(true);
      })
      .catch((err) => {
        if (err?.status === 401) {
          api.adminLogout();
          setIsAuthenticated(false);
        }
      });
  }, []);

  useEffect(() => {
    if (isAuthenticated) {
      loadStats();
    }
  }, [isAuthenticated, loadStats]);

  if (!isAuthenticated) {
    return (
      <AdminLoginScreen
        onLogin={() => {
          setIsAuthenticated(true);
          loadStats();
        }}
        language={language}
        onToggleLang={toggleLang}
        theme={theme}
        onSelectTheme={selectTheme}
      />
    );
  }

  const VIEWS = [
    { id: 'overview', label: t('admin.nav.overview'), enLabel: 'Overview' },
    {
      id: 'refunds',
      label: t('admin.nav.refunds'),
      enLabel: 'Refunds',
      badge: (s) => s?.refunds_pending,
    },
    { id: 'orders', label: t('admin.nav.orders'), enLabel: 'Orders' },
    {
      id: 'tasks',
      label: t('admin.nav.tasks'),
      enLabel: 'Queue',
      badge: (s) => s?.tasks_dead_letter,
    },
    { id: 'pricing', label: t('admin.nav.pricing'), enLabel: 'Pricing' },
    { id: 'logs', label: t('admin.nav.logs'), enLabel: 'API log' },
    { id: 'settings', label: t('admin.nav.settings'), enLabel: 'Settings' },
  ];

  return (
    <div className="flex min-h-screen flex-col bg-bg text-ink">
      <AppBar>
        <div className="mx-auto flex h-full max-w-6xl items-center justify-between px-4">
          <Brand label="Dynadot" subtitle="Admin" />
          <div className="flex items-center gap-2">
            <ThemePicker
              theme={theme}
              onSelect={selectTheme}
              label={t('theme.label')}
            />
            <LangToggle language={language} onToggle={toggleLang} />
            <button
              type="button"
              onClick={handleLogout}
              className="rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium text-muted transition-colors hover:border-faint hover:text-ink"
            >
              {t('admin.auth.logout')}
            </button>
          </div>
        </div>
      </AppBar>

      <div className="border-b border-line bg-surface/50">
        <div className="mx-auto flex max-w-6xl items-center gap-1 overflow-x-auto px-4 py-2">
          {VIEWS.map((item) => {
            const count = item.badge?.(stats);
            const active = view === item.id;
            return (
              <button
                key={item.id}
                type="button"
                aria-label={item.enLabel}
                onClick={() => setView(item.id)}
                className={`flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  active
                    ? 'bg-elevated text-ink shadow-sm'
                    : 'text-muted hover:text-ink'
                }`}
              >
                <span>{language === 'ru' ? item.label : item.enLabel}</span>
                {count ? (
                  <span
                    className={`rounded-full px-1.5 py-0.2 text-[10px] font-semibold tabular-nums leading-none ${
                      item.id === 'refunds'
                        ? 'bg-warn/20 text-warn'
                        : 'bg-danger/20 text-danger'
                    }`}
                  >
                    {count}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        {view === 'overview' ? <Overview stats={stats} language={language} /> : null}
        {view === 'refunds' ? (
          <Refunds onResolved={loadStats} language={language} />
        ) : null}
        {view === 'orders' ? <Orders language={language} /> : null}
        {view === 'tasks' ? (
          <Tasks onChange={loadStats} language={language} />
        ) : null}
        {view === 'pricing' ? <Pricing language={language} /> : null}
        {view === 'logs' ? <ApiLogs language={language} /> : null}
        {view === 'settings' ? <Settings language={language} /> : null}
      </main>
    </div>
  );
}

// ------------------------------------------------------------------ login screen

function AdminLoginScreen({ onLogin, language, onToggleLang, theme, onSelectTheme }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const t = makeTranslator(language);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!password) return;
    setLoading(true);
    setError(null);
    try {
      await api.adminLogin(username, password);
      onLogin();
    } catch {
      setError(t('admin.auth.error'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-bg text-ink">
      <header className="flex h-12 items-center justify-between border-b border-line px-6">
        <Brand label="Dynadot" subtitle="Admin" />
        <div className="flex items-center gap-2">
          <ThemePicker theme={theme} onSelect={onSelectTheme} label={t('theme.label')} />
          <LangToggle language={language} onToggle={onToggleLang} />
        </div>
      </header>

      <main className="flex flex-1 items-center justify-center p-4">
        <div className="w-full max-w-sm rounded-xl border border-line bg-surface p-6 shadow-xl">
          <div className="mb-6 flex flex-col items-center text-center">
            <Monogram className="mb-3 h-10 w-10 text-lg" />
            <h1 className="text-lg font-semibold text-ink">{t('admin.auth.title')}</h1>
            <p className="mt-1 text-xs text-muted">{t('admin.auth.subtitle')}</p>
          </div>

          {error ? (
            <div className="mb-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
              {error}
            </div>
          ) : null}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-muted">
                {t('admin.auth.login')}
              </label>
              <input
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-muted">
                {t('admin.auth.password')}
              </label>
              <input
                type="password"
                required
                autoFocus
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="btn-primary mt-2 flex w-full items-center justify-center gap-2 py-2 text-sm font-medium"
            >
              {loading ? <Spinner className="h-4 w-4" /> : null}
              {t('admin.auth.submit')}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}

// ------------------------------------------------------------------ overview

function Overview({ stats, language }) {
  if (!stats) return <SkeletonList rows={2} />;

  const isRu = language === 'ru';
  const cards = [
    {
      label: isRu ? 'Выручка' : 'Revenue',
      value: `${money(stats.revenue_rub)} ₽`,
      tone: 'ok',
    },
    { label: isRu ? 'Выполнено' : 'Completed', value: stats.orders_completed },
    {
      label: isRu ? 'Ожидают оплаты' : 'Awaiting payment',
      value: stats.orders_awaiting_payment,
    },
    {
      label: isRu ? 'Ожидают возврата' : 'Refunds pending',
      value: stats.refunds_pending,
      tone: stats.refunds_pending > 0 ? 'warn' : undefined,
    },
    { label: isRu ? 'Активные домены' : 'Active domains', value: stats.domains_active },
    { label: isRu ? 'Пользователи' : 'Users', value: stats.users },
    { label: isRu ? 'В очереди' : 'Queued tasks', value: stats.tasks_queued },
    {
      label: isRu ? 'Сбои (Dead Letter)' : 'Dead letter',
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

// ------------------------------------------------------------------ refunds

function Refunds({ onResolved, language }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(() => {
    setError(null);
    api
      .adminRefunds()
      .then((list) => setRows(Array.isArray(list) ? list : []))
      .catch(() => setError('Could not load pending refunds.'));
  }, []);

  useEffect(load, [load]);

  async function resolve(order) {
    const isRu = language === 'ru';
    const note = window.prompt(
      isRu
        ? `Пометить заказ #${order.id} возвращенным. Примечание:`
        : `Mark refund as settled for ${order.domain_name}. Note (e.g. transfer id):`,
      isRu ? 'Возврат средств клиенту через Platega' : 'Refund issued via gateway',
    );
    if (!note) return;

    setBusyId(order.id);
    try {
      await api.resolveRefund(order.id, note);
      load();
      onResolved?.();
    } catch {
      setError('Could not mark the refund as resolved.');
    } finally {
      setBusyId(null);
    }
  }

  if (error) return <ErrorNote message={error} onRetry={load} />;
  if (rows === null) return <SkeletonList rows={4} />;
  if (rows.length === 0) {
    return (
      <EmptyState
        icon="✓"
        title={language === 'ru' ? 'Нет ожидающих возвратов' : 'No refunds pending'}
      />
    );
  }

  const isRu = language === 'ru';
  return (
    <Table
      head={[
        isRu ? 'Домен' : 'Domain',
        isRu ? 'Сумма' : 'Amount',
        isRu ? 'Причина' : 'Reason',
        isRu ? 'Создан' : 'Created',
        '',
      ]}
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
          {when(order.created_at, language)}
        </span>,
        <button
          key="b"
          type="button"
          className="btn-ghost px-3 py-1.5 text-xs"
          onClick={() => resolve(order)}
          disabled={busyId === order.id}
        >
          {busyId === order.id ? <Spinner className="h-3 w-3" /> : null}
          {isRu ? 'Пометить возвращенным' : 'Mark refunded'}
        </button>,
      ])}
    />
  );
}

// -------------------------------------------------------------------- orders

function Orders({ language }) {
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

  const isRu = language === 'ru';
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1 text-xs">
        {statuses.map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setStatus(key)}
            className={`rounded-md px-2.5 py-1 font-medium transition-colors ${
              status === key
                ? 'bg-elevated text-ink shadow-sm'
                : 'text-muted hover:text-ink'
            }`}
          >
            {key === '' ? (isRu ? 'Все заказы' : 'All') : key}
          </button>
        ))}
      </div>

      {error ? <ErrorNote message={error} /> : null}
      {rows === null ? <SkeletonList rows={5} /> : null}
      {rows && rows.length === 0 ? (
        <EmptyState
          icon="∅"
          title={isRu ? 'Заказы не найдены' : 'No orders matched'}
        />
      ) : null}
      {rows && rows.length > 0 ? (
        <Table
          head={[
            isRu ? 'Заказ' : 'Order',
            isRu ? 'Домен' : 'Domain',
            isRu ? 'Статус' : 'Status',
            isRu ? 'Сумма' : 'Price',
            isRu ? 'Создан' : 'Created',
          ]}
          rows={rows.map((order) => [
            <span key="i" className="font-mono text-xs text-muted">
              {order.id.slice(0, 8)}
            </span>,
            <span key="d" className="font-medium">
              {order.domain_name}
            </span>,
            <StatusBadge key="s" status={order.status} label={order.status} />,
            <span key="p" className="tabular-nums">
              {money(order.price_rub)} ₽
            </span>,
            <span key="c" className="text-muted">
              {when(order.created_at, language)}
            </span>,
          ])}
        />
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------- tasks

function Tasks({ onChange, language }) {
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
    return (
      <EmptyState
        icon="✓"
        title={language === 'ru' ? 'Очередь задач пуста' : 'Queue is empty'}
      />
    );
  }

  const isRu = language === 'ru';
  return (
    <Table
      head={[
        isRu ? 'Операция' : 'Operation',
        isRu ? 'Статус' : 'Status',
        isRu ? 'Попытки' : 'Attempts',
        isRu ? 'Ошибка' : 'Error',
        isRu ? 'Запланировано' : 'Scheduled',
        '',
      ]}
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
          {when(task.scheduled_at, language)}
        </span>,
        task.status === 'dead_letter' || task.status === 'failed' ? (
          <button
            key="r"
            type="button"
            className="btn-ghost px-2 py-1 text-xs"
            onClick={() => retry(task)}
            disabled={busyId === task.id}
          >
            {busyId === task.id ? <Spinner className="h-3 w-3" /> : null}
            {isRu ? 'Повторить' : 'Retry'}
          </button>
        ) : (
          <span key="r" />
        ),
      ])}
    />
  );
}

// ------------------------------------------------------------------- pricing

function Pricing({ language }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [savingTld, setSavingTld] = useState(null);

  const load = useCallback(() => {
    setError(null);
    api
      .adminPricing()
      .then((list) => setRows(Array.isArray(list) ? list : []))
      .catch(() => setError('Could not load TLD pricing table.'));
  }, []);

  useEffect(load, [load]);

  async function save(tld, patch) {
    setSavingTld(tld);
    try {
      const updated = await api.updatePricing(tld, patch);
      setRows((prev) =>
        prev ? prev.map((r) => (r.tld === tld ? { ...r, ...updated } : r)) : [],
      );
    } catch {
      setError(`Could not update .${tld}`);
    } finally {
      setSavingTld(null);
    }
  }

  if (error) return <ErrorNote message={error} onRetry={load} />;
  if (rows === null) return <SkeletonList rows={5} />;

  const isRu = language === 'ru';
  return (
    <>
      <p className="mb-3 text-xs text-muted">
        {isRu
          ? 'Изменения применяются только к новым заказам — в существующих цена заморожена.'
          : 'Changes apply to new orders only — existing orders keep the price frozen.'}
      </p>
      <Table
        head={[
          isRu ? 'Зона' : 'Zone',
          isRu ? 'Себестоимость' : 'Cost',
          isRu ? 'Наценка %' : 'Markup %',
          isRu ? 'Фикс ₽' : 'Fixed ₽',
          isRu ? 'Активен' : 'Enabled',
          '',
        ]}
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
      className="w-20 rounded-md border border-line bg-surface px-2 py-1 text-xs tabular-nums text-ink focus:border-accent focus:outline-none"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => {
        const next = draft === '' ? null : Number(draft);
        if (next !== value) onSave(next);
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') event.currentTarget.blur();
      }}
    />
  );
}

// ---------------------------------------------------------------------- logs

function ApiLogs({ language }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    setError(null);
    api
      .adminApiLogs({ limit: 50 })
      .then((list) => setRows(Array.isArray(list) ? list : []))
      .catch(() => setError('Could not load API logs.'));
  }, []);

  useEffect(load, [load]);

  if (error) return <ErrorNote message={error} onRetry={load} />;
  if (rows === null) return <SkeletonList rows={5} />;
  if (rows.length === 0) {
    return (
      <EmptyState
        icon="∅"
        title={language === 'ru' ? 'Логов API пока нет' : 'No API calls yet'}
      />
    );
  }

  const isRu = language === 'ru';
  return (
    <Table
      head={[
        isRu ? 'Метод' : 'Method',
        isRu ? 'Путь' : 'Path',
        isRu ? 'Статус' : 'Status',
        'HTTP',
        isRu ? 'Время' : 'Duration',
        isRu ? 'Попытка' : 'Attempt',
        isRu ? 'Дата' : 'Timestamp',
      ]}
      rows={rows.map((log) => [
        <span key="m" className="font-mono text-xs">
          {log.method}
        </span>,
        <span key="p" className="max-w-xs truncate font-mono text-xs text-muted" title={log.path}>
          {log.path}
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
          {log.duration_ms ? `${Math.round(log.duration_ms)} ms` : '—'}
        </span>,
        <span key="a" className="tabular-nums text-muted">
          {log.attempt}
        </span>,
        <span key="w" className="text-muted">
          {when(log.created_at, language)}
        </span>,
      ])}
    />
  );
}

// ------------------------------------------------------------------ settings

function KeyBadge({ configured, language }) {
  const isRu = language === 'ru';
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
        configured ? 'bg-ok/15 text-ok' : 'bg-muted/15 text-muted'
      }`}
    >
      {configured
        ? isRu
          ? 'Активен'
          : 'Configured'
        : isRu
          ? 'Не задан'
          : 'Not set'}
    </span>
  );
}

function TestConnectionButton({ provider, label, language }) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);
  const isRu = language === 'ru';

  async function handleTest() {
    setTesting(true);
    setResult(null);
    try {
      const res = await api.testConnection(provider);
      setResult(res);
    } catch {
      setResult({
        ok: false,
        message: isRu
          ? 'Сетевая ошибка при проверке API'
          : 'Network error during connection test',
      });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="mt-2 space-y-1.5">
      <button
        type="button"
        onClick={handleTest}
        disabled={testing}
        className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1 text-xs font-medium text-ink transition-colors hover:border-accent hover:text-accent disabled:opacity-50"
      >
        {testing ? <Spinner className="h-3 w-3" /> : <span>⚡</span>}
        <span>
          {testing
            ? isRu
              ? 'Проверка соединения…'
              : 'Testing connection…'
            : isRu
              ? `Проверить подключение ${label ? `(${label})` : ''}`
              : `Test connection ${label ? `(${label})` : ''}`}
        </span>
      </button>

      {result ? (
        <div
          className={`rounded-lg border px-3 py-2 text-xs font-medium transition-all ${
            result.ok
              ? 'border-ok/30 bg-ok/10 text-ok'
              : 'border-danger/30 bg-danger/10 text-danger'
          }`}
        >
          <span className="mr-1.5 font-bold">{result.ok ? '✓' : '✕'}</span>
          {result.message}
        </div>
      ) : null}
    </div>
  );
}

function Settings({ language }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [error, setError] = useState(null);

  // Financial
  const [fxRate, setFxRate] = useState('95.0');
  const [markup, setMarkup] = useState('25.0');
  const [vpsTermDays, setVpsTermDays] = useState('30');

  // Dynadot
  const [baseUrl, setBaseUrl] = useState('https://api-sandbox.dynadot.com');
  const [dynadotKey, setDynadotKey] = useState('');
  const [dynadotSecret, setDynadotSecret] = useState('');

  // Platega
  const [merchantId, setMerchantId] = useState('');
  const [plategaSecret, setPlategaSecret] = useState('');
  const [plategaBaseUrl, setPlategaBaseUrl] = useState('https://app.platega.io');
  const [paymentMethod, setPaymentMethod] = useState('2');

  // VPS
  const [vpsProvider, setVpsProvider] = useState('mock');
  const [timewebToken, setTimewebToken] = useState('');
  const [aezaToken, setAezaToken] = useState('');
  const [pqToken, setPqToken] = useState('');
  const [realitySni, setRealitySni] = useState('www.microsoft.com');

  // Telegram
  const [telegramToken, setTelegramToken] = useState('');
  const [supportUsername, setSupportUsername] = useState('');

  // Worker
  const [workerEnabled, setWorkerEnabled] = useState(true);

  const t = makeTranslator(language);
  const isRu = language === 'ru';

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .adminSettings()
      .then((cfg) => {
        setData(cfg);
        setFxRate(String(cfg.usd_to_rub_rate ?? 95.0));
        setMarkup(String(cfg.default_markup_percent ?? 25.0));
        setVpsTermDays(String(cfg.vps_term_days ?? 30));
        setBaseUrl(cfg.dynadot_base_url || 'https://api-sandbox.dynadot.com');
        setMerchantId(cfg.platega_merchant_id || '');
        setPlategaBaseUrl(cfg.platega_base_url || 'https://app.platega.io');
        setPaymentMethod(String(cfg.platega_payment_method ?? 2));
        setVpsProvider(cfg.vps_provider || 'mock');
        setRealitySni(cfg.vps_reality_sni || 'www.microsoft.com');
        setSupportUsername(cfg.support_username || '');
        setWorkerEnabled(Boolean(cfg.registration_worker_enabled));
      })
      .catch(() => setError('Could not load system settings.'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    setSavedSuccess(false);
    setError(null);

    const payload = {
      usd_to_rub_rate: Number(fxRate),
      default_markup_percent: Number(markup),
      vps_term_days: Number(vpsTermDays),
      dynadot_base_url: baseUrl,
      platega_merchant_id: merchantId.trim() || null,
      platega_base_url: plategaBaseUrl.trim() || undefined,
      platega_payment_method: Number(paymentMethod) || 2,
      vps_provider: vpsProvider,
      vps_reality_sni: realitySni.trim() || undefined,
      support_username: supportUsername.trim() || null,
      registration_worker_enabled: workerEnabled,
    };

    if (dynadotKey.trim()) payload.dynadot_api_key = dynadotKey.trim();
    if (dynadotSecret.trim()) payload.dynadot_api_secret = dynadotSecret.trim();
    if (plategaSecret.trim()) payload.platega_secret = plategaSecret.trim();
    if (timewebToken.trim()) payload.timeweb_api_token = timewebToken.trim();
    if (aezaToken.trim()) payload.aeza_api_token = aezaToken.trim();
    if (pqToken.trim()) payload.pq_hosting_api_token = pqToken.trim();
    if (telegramToken.trim()) payload.telegram_bot_token = telegramToken.trim();

    try {
      const updated = await api.updateAdminSettings(payload);
      setData(updated);
      setDynadotKey('');
      setDynadotSecret('');
      setPlategaSecret('');
      setTimewebToken('');
      setAezaToken('');
      setPqToken('');
      setTelegramToken('');
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 4000);
    } catch {
      setError('Could not update settings.');
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <SkeletonList rows={5} />;
  if (error && !data) return <ErrorNote message={error} onRetry={load} />;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h2 className="text-base font-semibold text-ink">{t('admin.settings.title')}</h2>
        <p className="mt-1 text-xs text-muted">{t('admin.settings.subtitle')}</p>
      </div>

      {savedSuccess ? (
        <div className="rounded-lg border border-ok/30 bg-ok/10 px-4 py-2.5 text-xs font-medium text-ok">
          ✓ {t('admin.settings.saved')}
        </div>
      ) : null}

      {error ? <ErrorNote message={error} /> : null}

      <form onSubmit={handleSave} className="space-y-5">
        {/* 1. Finance & Pricing */}
        <div className="card space-y-4 p-5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
            {t('admin.settings.finance')}
          </h3>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-ink">
                {t('admin.settings.fx_rate')} (₽)
              </label>
              <input
                type="number"
                step="0.1"
                min="1"
                required
                value={fxRate}
                onChange={(e) => setFxRate(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm tabular-nums text-ink focus:border-accent focus:outline-none"
              />
              <p className="mt-1 text-[11px] text-muted">{t('admin.settings.fx_hint')}</p>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-ink">
                {t('admin.settings.markup')} (%)
              </label>
              <input
                type="number"
                step="0.5"
                min="0"
                required
                value={markup}
                onChange={(e) => setMarkup(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm tabular-nums text-ink focus:border-accent focus:outline-none"
              />
              <p className="mt-1 text-[11px] text-muted">{t('admin.settings.markup_hint')}</p>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-ink">
                {t('admin.settings.vps_term')}
              </label>
              <input
                type="number"
                min="1"
                required
                value={vpsTermDays}
                onChange={(e) => setVpsTermDays(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm tabular-nums text-ink focus:border-accent focus:outline-none"
              />
            </div>
          </div>
        </div>

        {/* 2. Dynadot Domain Registrar */}
        <div className="card space-y-4 p-5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
              {t('admin.settings.dynadot')}
            </h3>
            <KeyBadge configured={data?.dynadot_configured} language={language} />
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-ink">
              {t('admin.settings.env_mode')}
            </label>
            <select
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
            >
              <option value="https://api-sandbox.dynadot.com">
                {t('admin.settings.sandbox')}
              </option>
              <option value="https://api.dynadot.com">
                {t('admin.settings.production')}
              </option>
            </select>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-ink">
                  {t('admin.settings.dynadot_key')}
                </label>
                <KeyBadge configured={data?.has_dynadot_api_key} language={language} />
              </div>
              <input
                type="password"
                placeholder={data?.has_dynadot_api_key ? '••••••••••••' : 'Вставьте API Key'}
                value={dynadotKey}
                onChange={(e) => setDynadotKey(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-ink">
                  {t('admin.settings.dynadot_secret')}
                </label>
                <KeyBadge configured={data?.has_dynadot_api_secret} language={language} />
              </div>
              <input
                type="password"
                placeholder={data?.has_dynadot_api_secret ? '••••••••••••' : 'Вставьте API Secret'}
                value={dynadotSecret}
                onChange={(e) => setDynadotSecret(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>
          </div>

          <TestConnectionButton provider="dynadot" label="Dynadot API" language={language} />
        </div>

        {/* 3. Platega Payment Gateway */}
        <div className="card space-y-4 p-5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
              {t('admin.settings.platega_section')}
            </h3>
            <KeyBadge configured={data?.platega_configured} language={language} />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-ink">
                {t('admin.settings.platega_merchant')}
              </label>
              <input
                type="text"
                placeholder="2793"
                value={merchantId}
                onChange={(e) => setMerchantId(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-ink">
                  {t('admin.settings.platega_secret')}
                </label>
                <KeyBadge configured={data?.has_platega_secret} language={language} />
              </div>
              <input
                type="password"
                placeholder={data?.has_platega_secret ? '••••••••••••' : 'Вставьте секрет'}
                value={plategaSecret}
                onChange={(e) => setPlategaSecret(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>
          </div>

          <TestConnectionButton provider="platega" label="Platega" language={language} />
        </div>

        {/* 4. VPS Providers */}
        <div className="card space-y-4 p-5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
              {t('admin.settings.vps_section')}
            </h3>
            <KeyBadge configured={data?.vps_configured} language={language} />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-ink">
                {t('admin.settings.vps_active')}
              </label>
              <select
                value={vpsProvider}
                onChange={(e) => setVpsProvider(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              >
                <option value="timeweb">Timeweb Cloud</option>
                <option value="aeza">Aeza</option>
                <option value="pq_hosting">PQ.Hosting</option>
                <option value="mock">Тестовый Mock (без списаний)</option>
              </select>
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-ink">
                {t('admin.settings.vps_sni')}
              </label>
              <input
                type="text"
                value={realitySni}
                onChange={(e) => setRealitySni(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>
          </div>

          <div className="space-y-3 pt-2">
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-ink">
                  {t('admin.settings.timeweb_token')}
                </label>
                <KeyBadge configured={data?.has_timeweb_api_token} language={language} />
              </div>
              <input
                type="password"
                placeholder={data?.has_timeweb_api_token ? '••••••••••••' : 'Bearer токен Timeweb'}
                value={timewebToken}
                onChange={(e) => setTimewebToken(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-ink">
                  {t('admin.settings.aeza_token')}
                </label>
                <KeyBadge configured={data?.has_aeza_api_token} language={language} />
              </div>
              <input
                type="password"
                placeholder={data?.has_aeza_api_token ? '••••••••••••' : 'API ключ Aeza'}
                value={aezaToken}
                onChange={(e) => setAezaToken(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-ink">
                  {t('admin.settings.pq_token')}
                </label>
                <KeyBadge configured={data?.has_pq_hosting_api_token} language={language} />
              </div>
              <input
                type="password"
                placeholder={data?.has_pq_hosting_api_token ? '••••••••••••' : 'API ключ PQ.Hosting'}
                value={pqToken}
                onChange={(e) => setPqToken(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-2 pt-2 border-t border-line/50">
            <TestConnectionButton provider="timeweb" label="Timeweb" language={language} />
            <TestConnectionButton provider="aeza" label="Aeza" language={language} />
            <TestConnectionButton provider="pq_hosting" label="PQ.Hosting" language={language} />
          </div>
        </div>

        {/* 5. Telegram Bot & Support */}
        <div className="card space-y-4 p-5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
              {t('admin.settings.telegram_section')}
            </h3>
            <KeyBadge configured={data?.telegram_configured} language={language} />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <div className="mb-1 flex items-center justify-between">
                <label className="text-xs font-medium text-ink">
                  {t('admin.settings.telegram_token')}
                </label>
                <KeyBadge configured={data?.has_telegram_bot_token} language={language} />
              </div>
              <input
                type="password"
                placeholder={data?.has_telegram_bot_token ? '••••••••••••' : '123456:ABC-DEF...'}
                value={telegramToken}
                onChange={(e) => setTelegramToken(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-ink">
                {t('admin.settings.support_user')}
              </label>
              <input
                type="text"
                placeholder="@support"
                value={supportUsername}
                onChange={(e) => setSupportUsername(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>
          </div>

          <TestConnectionButton provider="telegram" label="Telegram Bot" language={language} />
        </div>

        {/* 6. Worker & System */}
        <div className="card space-y-3 p-5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
            {t('admin.settings.worker')}
          </h3>

          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={workerEnabled}
              onChange={(e) => setWorkerEnabled(e.target.checked)}
              className="h-4 w-4 rounded border-line bg-bg text-accent focus:ring-0"
            />
            <span className="text-xs font-medium text-ink">
              {t('admin.settings.worker_enable')}
            </span>
          </label>
        </div>

        {/* System info */}
        <div className="card flex items-center justify-between p-4 text-xs text-muted">
          <span>
            {isRu ? 'Окружение:' : 'Environment:'}{' '}
            <strong className="text-ink font-medium">{data?.environment || 'prod'}</strong>
          </span>
          <span>
            {isRu ? 'Версия:' : 'Version:'}{' '}
            <strong className="text-ink font-medium">{data?.app_version || '0.1.0'}</strong>
          </span>
        </div>

        <button
          type="submit"
          disabled={saving}
          className="btn-primary flex items-center justify-center gap-2 px-6 py-2.5 text-xs font-medium"
        >
          {saving ? <Spinner className="h-3.5 w-3.5" /> : null}
          {saving ? t('admin.settings.saving') : t('admin.settings.save')}
        </button>
      </form>
    </div>
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
