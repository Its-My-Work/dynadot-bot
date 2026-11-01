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
  const [needsSetup, setNeedsSetup] = useState(false);
  const [setupChecked, setSetupChecked] = useState(false);
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

  useEffect(() => {
    api
      .adminSetupStatus()
      .then((res) => {
        setNeedsSetup(Boolean(res?.needs_setup));
      })
      .catch(() => {
        setNeedsSetup(false);
      })
      .finally(() => {
        setSetupChecked(true);
      });
  }, []);

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

  if (!isAuthenticated && needsSetup && setupChecked) {
    return (
      <AdminSetupScreen
        onDone={() => {
          setNeedsSetup(false);
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
      id: 'domains',
      label: t('admin.nav.domains') || 'Домены',
      enLabel: 'Domains',
    },
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
    { id: 'categories', label: t('admin.nav.categories'), enLabel: 'Categories' },
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
        {view === 'domains' ? <DomainsManagement language={language} /> : null}
        {view === 'refunds' ? (
          <Refunds onResolved={loadStats} language={language} />
        ) : null}
        {view === 'orders' ? <Orders language={language} /> : null}
        {view === 'tasks' ? (
          <Tasks onChange={loadStats} language={language} />
        ) : null}
        {view === 'pricing' ? <Pricing language={language} /> : null}
        {view === 'categories' ? <CategoriesView language={language} /> : null}
        {view === 'logs' ? <ApiLogs language={language} /> : null}
        {view === 'settings' ? <Settings language={language} /> : null}
      </main>
    </div>
  );
}

// ------------------------------------------------------------------ setup screen

function AdminSetupScreen({ onDone, language, onToggleLang, theme, onSelectTheme }) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const t = makeTranslator(language);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!email || !password) return;
    if (password !== confirmPassword) {
      setError(language === 'ru' ? 'Пароли не совпадают' : 'Passwords do not match');
      return;
    }
    if (password.length < 6) {
      setError(language === 'ru' ? 'Пароль должен содержать минимум 6 символов' : 'Password must be at least 6 characters');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.adminSetup(email, password, name || null);
      onDone();
    } catch (err) {
      setError(err?.detail || err?.message || (language === 'ru' ? 'Ошибка настройки администратора' : 'Setup failed'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col bg-bg text-ink">
      <header className="flex h-12 items-center justify-between border-b border-line px-6">
        <Brand label="Dynadot" subtitle="Setup" />
        <div className="flex items-center gap-2">
          <ThemePicker theme={theme} onSelect={onSelectTheme} label={t('theme.label')} />
          <LangToggle language={language} onToggle={onToggleLang} />
        </div>
      </header>

      <main className="flex flex-1 items-center justify-center p-4">
        <div className="w-full max-w-md rounded-xl border border-line bg-surface p-6 shadow-xl">
          <div className="mb-6 flex flex-col items-center text-center">
            <div className="mb-3 text-3xl">🛠</div>
            <h1 className="text-lg font-semibold text-ink">
              {language === 'ru' ? 'Первоначальная настройка' : 'First-time Setup'}
            </h1>
            <p className="mt-1 text-xs text-muted leading-relaxed">
              {language === 'ru'
                ? 'В системе ещё нет администратора. Создайте учетную запись главного администратора для управления системой.'
                : 'No administrator configured. Create the primary administrator account to manage the system.'}
            </p>
          </div>

          {error ? (
            <div className="mb-4 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
              {error}
            </div>
          ) : null}

          <form onSubmit={handleSubmit} className="space-y-3.5">
            <div>
              <label htmlFor="setup-email" className="mb-1 block text-xs font-medium text-muted">
                {language === 'ru' ? 'Email администратора' : 'Admin Email'}
              </label>
              <input
                id="setup-email"
                type="email"
                required
                autoFocus
                placeholder="admin@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <label htmlFor="setup-name" className="mb-1 block text-xs font-medium text-muted">
                {language === 'ru' ? 'Имя администратора (необязательно)' : 'Admin Name (optional)'}
              </label>
              <input
                id="setup-name"
                type="text"
                placeholder="Admin"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <label htmlFor="setup-password" className="mb-1 block text-xs font-medium text-muted">
                {language === 'ru' ? 'Пароль (не менее 6 символов)' : 'Password (min 6 chars)'}
              </label>
              <input
                id="setup-password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <label htmlFor="setup-confirm" className="mb-1 block text-xs font-medium text-muted">
                {language === 'ru' ? 'Подтверждение пароля' : 'Confirm Password'}
              </label>
              <input
                id="setup-confirm"
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="btn-primary mt-3 flex w-full items-center justify-center gap-2 py-2 text-sm font-medium"
            >
              {loading ? <Spinner className="h-4 w-4" /> : null}
              {language === 'ru' ? 'Создать аккаунт администратора' : 'Create Admin Account'}
            </button>
          </form>
        </div>
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
              <label htmlFor="admin-username" className="mb-1 block text-xs font-medium text-muted">
                {t('admin.auth.username')}
              </label>
              <input
                id="admin-username"
                type="text"
                required
                placeholder={language === 'ru' ? 'admin@example.com или логин' : 'admin@example.com or username'}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <label htmlFor="admin-password" className="mb-1 block text-xs font-medium text-muted">
                {t('admin.auth.password')}
              </label>
              <input
                id="admin-password"
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
      delta: stats.revenue_delta_pct,
      tone: 'ok',
      icon: '💰',
    },
    {
      label: isRu ? 'Выполнено' : 'Completed',
      value: stats.orders_completed,
      delta: stats.orders_completed_delta_pct,
      icon: '✓',
    },
    {
      label: isRu ? 'Ожидают оплаты' : 'Awaiting payment',
      value: stats.orders_awaiting_payment,
      icon: '⏳',
    },
    {
      label: isRu ? 'Ожидают возврата' : 'Refunds pending',
      value: stats.refunds_pending,
      tone: stats.refunds_pending > 0 ? 'warn' : undefined,
      icon: '↩',
    },
    {
      label: isRu ? 'Активные домены' : 'Active domains',
      value: stats.domains_active,
      delta: stats.domains_delta_pct,
      icon: '🌐',
    },
    {
      label: isRu ? 'Пользователи' : 'Users',
      value: stats.users,
      delta: stats.users_delta_pct,
      icon: '👤',
    },
    { label: isRu ? 'В очереди' : 'Queued tasks', value: stats.tasks_queued, icon: '⚙' },
    {
      label: isRu ? 'Сбои (Dead Letter)' : 'Dead letter',
      value: stats.tasks_dead_letter,
      tone: stats.tasks_dead_letter > 0 ? 'danger' : undefined,
      icon: '✕',
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {cards.map((card) => (
        <div key={card.label} className="card p-4">
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted">{card.label}</p>
            <span className="text-base leading-none opacity-60">{card.icon}</span>
          </div>
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
            {card.value ?? '—'}
          </p>
          {card.delta !== null && card.delta !== undefined ? (
            <p
              className={`mt-0.5 text-[11px] font-medium tabular-nums ${
                card.delta >= 0 ? 'text-ok' : 'text-danger'
              }`}
            >
              {card.delta >= 0 ? '+' : ''}{card.delta}% vs вчера
            </p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ domains & ownership view

function DomainsManagement({ language }) {
  const [items, setItems] = useState([]);
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);
  const [selectedDomain, setSelectedDomain] = useState(null);
  const [assignTargetUserId, setAssignTargetUserId] = useState('');
  const [assignTargetEmail, setAssignTargetEmail] = useState('');
  const [assigning, setAssigning] = useState(false);

  const t = makeTranslator(language);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [domRes, userRes] = await Promise.all([
        api.adminDomains().catch(() => ({ items: [] })),
        api.adminUsers().catch(() => []),
      ]);
      setItems(domRes?.items || []);
      setUsers(Array.isArray(userRes) ? userRes : []);
    } catch {
      setError(language === 'ru' ? 'Не удалось загрузить домены' : 'Failed to load domains');
    } finally {
      setLoading(false);
    }
  }, [language]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await api.adminSyncDomains();
      await loadData();
      setToast({ message: res.message || 'Синхронизация завершена', tone: 'ok' });
    } catch (err) {
      setToast({
        message: err?.detail || (language === 'ru' ? 'Ошибка синхронизации' : 'Sync error'),
        tone: 'danger',
      });
    } finally {
      setSyncing(false);
    }
  };

  const handleAssign = async (e) => {
    e.preventDefault();
    if (!selectedDomain) return;
    setAssigning(true);
    try {
      const payload = assignTargetUserId
        ? { userId: assignTargetUserId }
        : { email: assignTargetEmail.trim() };
      await api.adminAssignDomain(selectedDomain.id, payload);
      await loadData();
      setSelectedDomain(null);
      setAssignTargetUserId('');
      setAssignTargetEmail('');
      setToast({
        message: language === 'ru' ? 'Владелец домена изменен!' : 'Owner assigned successfully!',
        tone: 'ok',
      });
    } catch (err) {
      setToast({
        message: err?.detail || (language === 'ru' ? 'Ошибка изменения владельца' : 'Failed to assign owner'),
        tone: 'danger',
      });
    } finally {
      setAssigning(false);
    }
  };

  if (loading && items.length === 0) {
    return <SkeletonList rows={4} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
        <div>
          <h1 className="text-lg font-semibold text-ink">{t('admin.domains.title')}</h1>
          <p className="text-xs text-muted">{t('admin.domains.subtitle')}</p>
        </div>
        <button
          type="button"
          onClick={handleSync}
          disabled={syncing}
          className="btn-primary inline-flex items-center gap-2 text-xs font-semibold px-3.5 py-2 cursor-pointer shadow-sm hover:opacity-95"
        >
          {syncing ? <Spinner className="h-3.5 w-3.5" /> : <span>🔄</span>}
          <span>{syncing ? t('admin.domains.syncing') : t('admin.domains.sync_btn')}</span>
        </button>
      </div>

      {error ? (
        <ErrorNote message={error} onRetry={loadData} retryLabel={language === 'ru' ? 'Повторить' : 'Retry'} />
      ) : null}

      {items.length === 0 ? (
        <EmptyState
          title={language === 'ru' ? 'Нет доменов' : 'No domains'}
          description={t('admin.domains.empty')}
          actionLabel={t('admin.domains.sync_btn')}
          onAction={handleSync}
        />
      ) : (
        <div className="overflow-hidden rounded-xl border border-line bg-surface shadow-sm">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-line bg-bg/50 text-[11px] font-semibold text-muted">
              <tr>
                <th className="px-4 py-3">Домен</th>
                <th className="px-4 py-3">Статус</th>
                <th className="px-4 py-3">Срок действия</th>
                <th className="px-4 py-3">Владелец в сервисе</th>
                <th className="px-4 py-3 text-right">Действия</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line text-ink">
              {items.map((dom) => {
                const ownerText = dom.owner_email || (dom.owner_username ? `@${dom.owner_username}` : null);
                return (
                  <tr key={dom.id} className="hover:bg-bg/40 transition-colors">
                    <td className="px-4 py-3.5">
                      <div className="font-bold font-mono text-ink text-sm flex items-center gap-1.5">
                        <span>🌐</span>
                        <span>{dom.name}</span>
                      </div>
                      {dom.nameservers ? (
                        <div className="text-[10px] text-muted font-mono mt-0.5 truncate max-w-xs">
                          NS: {dom.nameservers}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-4 py-3.5">
                      <span className="badge text-[10px] font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 px-2 py-0.5">
                        {dom.status}
                      </span>
                      {dom.auto_renew ? (
                        <span className="ml-1 text-[10px] text-muted">автопродление</span>
                      ) : null}
                    </td>
                    <td className="px-4 py-3.5 font-mono text-[11px] text-muted">
                      {dom.expires_at ? (
                        <div>
                          <div>до {when(dom.expires_at, language)}</div>
                        </div>
                      ) : '—'}
                    </td>
                    <td className="px-4 py-3.5">
                      {ownerText ? (
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs">👤</span>
                          <span className="font-medium text-ink">{ownerText}</span>
                        </div>
                      ) : (
                        <span className="badge text-[10px] bg-warn/15 text-warn border border-warn/30 font-semibold px-2 py-0.5">
                          Не привязан
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3.5 text-right space-x-2">
                      <button
                        type="button"
                        onClick={() => {
                          setSelectedDomain(dom);
                          setAssignTargetUserId(dom.user_id || '');
                        }}
                        className="rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-semibold text-accent hover:border-accent/40 hover:bg-accent/10 transition-colors cursor-pointer"
                      >
                        Изменить владельца
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal: Change Owner */}
      {selectedDomain ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-xl border border-line bg-surface p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-line pb-3">
              <h2 className="text-sm font-bold text-ink flex items-center gap-2">
                <span>👤</span>
                <span>Закрепление домена: {selectedDomain.name}</span>
              </h2>
              <button
                type="button"
                onClick={() => setSelectedDomain(null)}
                className="text-muted hover:text-ink cursor-pointer"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleAssign} className="space-y-4 text-xs">
              <div>
                <label className="mb-1.5 block font-medium text-muted">
                  Выберите пользователя из базы:
                </label>
                <select
                  value={assignTargetUserId}
                  onChange={(e) => {
                    setAssignTargetUserId(e.target.value);
                    setAssignTargetEmail('');
                  }}
                  className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-ink focus:border-accent outline-none"
                >
                  <option value="">-- Выбрать пользователя --</option>
                  {users.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.email || `@${u.username || u.telegram_id}`} ({u.role === 'admin' ? '👑 Админ' : 'Пользователь'})
                    </option>
                  ))}
                </select>
              </div>

              <div className="relative flex py-1 items-center">
                <div className="flex-grow border-t border-line"></div>
                <span className="flex-shrink mx-3 text-[10px] text-muted uppercase">или укажите Email вручную</span>
                <div className="flex-grow border-t border-line"></div>
              </div>

              <div>
                <label className="mb-1 block font-medium text-muted">
                  Email пользователя:
                </label>
                <input
                  type="email"
                  placeholder="user@example.com"
                  value={assignTargetEmail}
                  onChange={(e) => {
                    setAssignTargetEmail(e.target.value);
                    setAssignTargetUserId('');
                  }}
                  className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-ink focus:border-accent outline-none"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-2 border-t border-line">
                <button
                  type="button"
                  onClick={() => setSelectedDomain(null)}
                  className="px-3 py-1.5 rounded-lg border border-line text-muted hover:text-ink cursor-pointer"
                >
                  Отмена
                </button>
                <button
                  type="submit"
                  disabled={assigning || (!assignTargetUserId && !assignTargetEmail)}
                  className="btn-primary px-4 py-1.5 text-xs font-semibold cursor-pointer disabled:opacity-50"
                >
                  {assigning ? 'Сохранение...' : 'Сохранить владельца'}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      {toast ? (
        <div className="fixed bottom-4 right-4 z-50">
          <div
            className={`rounded-xl px-4 py-2.5 text-xs font-medium shadow-lg border ${
              toast.tone === 'danger'
                ? 'bg-rose-950/90 text-rose-200 border-rose-500/40'
                : 'bg-emerald-950/90 text-emerald-200 border-emerald-500/40'
            }`}
          >
            {toast.message}
          </div>
        </div>
      ) : null}
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
    if (!confirm(`Mark refund as settled for ${order.domain_name}?`)) return;

    setBusyId(order.id);
    try {
      await api.resolveRefund(order.id, 'Refund issued via gateway');
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
        title={language === 'ru' ? 'Нет ожидающих возвратов' : 'No pending refunds'}
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

  const STATUS_COLORS = {
    draft: 'bg-muted/15 text-muted',
    awaiting_payment: 'bg-warn/15 text-warn',
    paid: 'bg-accent/15 text-accent',
    registering: 'bg-accent/15 text-accent',
    completed: 'bg-ok/15 text-ok',
    failed: 'bg-danger/15 text-danger',
    refund_required: 'bg-warn/20 text-warn',
    refunded: 'bg-muted/15 text-muted',
    cancelled: 'bg-muted/15 text-muted',
  };

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
                ? key === ''
                  ? 'bg-elevated text-ink shadow-sm'
                  : `shadow-sm ${STATUS_COLORS[key] || 'bg-elevated text-ink'} ring-1 ring-inset ring-current/20`
                : key === ''
                  ? 'text-muted hover:text-ink'
                  : `${STATUS_COLORS[key] || 'text-muted'} opacity-50 hover:opacity-100`
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
            isRu ? 'Себестоимость' : 'Cost',
            isRu ? 'Наценка' : 'Markup',
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
            <span key="cost" className="tabular-nums text-muted">
              {order.cost_usd !== null && order.cost_usd !== undefined
                ? `$${Number(order.cost_usd).toFixed(2)}`
                : '—'}
            </span>,
            <span key="m" className="tabular-nums text-muted">
              {order.markup_percent !== null && order.markup_percent !== undefined
                ? `${order.markup_percent}%`
                : '—'}
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
  const [notice, setNotice] = useState(null);
  const [savingTld, setSavingTld] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newTld, setNewTld] = useState('');
  const [newCost, setNewCost] = useState('');
  const [newMarkup, setNewMarkup] = useState('');
  const [newFixed, setNewFixed] = useState('');
  const [adding, setAdding] = useState(false);

  const isRu = language === 'ru';

  const load = useCallback(() => {
    setError(null);
    api
      .adminPricing()
      .then((list) => setRows(Array.isArray(list) ? list : []))
      .catch(() => setError(isRu ? 'Не удалось загрузить цены TLD' : 'Could not load TLD pricing table.'));
  }, [isRu]);

  useEffect(load, [load]);

  async function save(tld, patch) {
    setSavingTld(tld);
    setError(null);
    try {
      const updated = await api.updatePricing(tld, patch);
      setRows((prev) =>
        prev ? prev.map((r) => (r.tld === tld ? { ...r, ...updated } : r)) : [],
      );
    } catch {
      setError(isRu ? `Не удалось обновить .${tld}` : `Could not update .${tld}`);
    } finally {
      setSavingTld(null);
    }
  }

  async function removeTld(tld) {
    if (!confirm(isRu ? `Удалить зону .${tld}?` : `Delete zone .${tld}?`)) return;
    setSavingTld(tld);
    try {
      await api.deletePricing(tld);
      setRows((prev) => (prev ? prev.filter((r) => r.tld !== tld) : []));
    } catch {
      setError(isRu ? `Не удалось удалить .${tld}` : `Could not delete .${tld}`);
    } finally {
      setSavingTld(null);
    }
  }

  async function syncWithDynadot() {
    setSyncing(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.syncTldPrices();
      setNotice(res.message || (isRu ? 'Цены синхронизированы' : 'Prices synced'));
      load();
    } catch (err) {
      setError(isRu ? `Ошибка синхронизации: ${err.message}` : `Sync error: ${err.message}`);
    } finally {
      setSyncing(false);
    }
  }

  async function handleAddTld(e) {
    e.preventDefault();
    const cleanTld = newTld.trim().replace(/^\./, '').toLowerCase();
    if (!cleanTld) return;
    setAdding(true);
    setError(null);
    try {
      await api.createPricing({
        tld: cleanTld,
        register_cost_usd: Number(newCost) || 0,
        markup_percent: newMarkup === '' ? null : Number(newMarkup),
        fixed_price_rub: newFixed === '' ? null : Number(newFixed),
        is_enabled: true,
        is_featured: false,
      });
      setNewTld('');
      setNewCost('');
      setNewMarkup('');
      setNewFixed('');
      setShowAddForm(false);
      load();
    } catch (err) {
      setError(isRu ? `Не удалось добавить зону: ${err.message}` : `Could not add zone: ${err.message}`);
    } finally {
      setAdding(false);
    }
  }

  if (error) return <ErrorNote message={error} onRetry={load} />;
  if (rows === null) return <SkeletonList rows={5} />;

  return (
    <div className="space-y-4">
      {notice && (
        <div className="rounded-lg border border-ok/30 bg-ok/10 p-3 text-xs text-ok flex justify-between items-center">
          <span>✓ {notice}</span>
          <button type="button" onClick={() => setNotice(null)} className="text-muted hover:text-ink font-bold">×</button>
        </div>
      )}

      <p className="text-xs text-muted">
        {isRu
          ? 'Изменения применяются только к новым заказам — в существующих цена заморожена.'
          : 'Changes apply to new orders only — existing orders keep the price frozen.'}
      </p>

      {/* Pricing explanation */}
      <div className="card p-3.5 space-y-1.5 text-xs text-muted">
        <p className="font-medium text-ink flex items-center gap-1.5">
          <span>⚙️</span>
          <span>{isRu ? 'Логика формирования розничной цены' : 'Retail Pricing Formula'}</span>
        </p>
        <p>
          {isRu
            ? '1. Если задан «Фикс ₽» — клиент платит фиксированную цену (независимо от курса доллара).'
            : '1. If "Fixed ₽" is set — customer pays this exact fixed amount.'}
        </p>
        <p>
          {isRu
            ? '2. Если «Фикс ₽» пуст — цена рассчитывается: Себестоимость $ × (1 + Наценка% / 100) × Курс USD.'
            : '2. If "Fixed ₽" is empty — price is: Cost $ × (1 + Markup% / 100) × USD Rate.'}
        </p>
        <p>
          {isRu
            ? '3. Если «Наценка%» пуста — применяется базовая наценка из вкладки «Настройки».'
            : '3. If "Markup%" is empty — default markup from "Settings" tab applies.'}
        </p>
      </div>

      {/* Actions toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={syncWithDynadot}
            disabled={syncing}
            className="btn-ghost text-xs px-3 py-1.5 flex items-center gap-1.5"
          >
            {syncing ? <Spinner className="h-3 w-3" /> : <span>🔄</span>}
            <span>{isRu ? 'Синхронизировать себестоимость с Dynadot' : 'Sync wholesale costs from Dynadot'}</span>
          </button>
        </div>

        <button
          type="button"
          onClick={() => setShowAddForm(!showAddForm)}
          className="btn-primary text-xs px-3 py-1.5"
        >
          {showAddForm ? (isRu ? '✕ Закрыть форму' : '✕ Cancel') : (isRu ? '+ Добавить зону TLD' : '+ Add TLD zone')}
        </button>
      </div>

      {/* Add TLD form */}
      {showAddForm && (
        <form onSubmit={handleAddTld} className="card p-4 space-y-3 bg-surface/70 border border-line">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-ink">
            {isRu ? 'Добавить новую доменную зону' : 'Add New TLD Zone'}
          </h4>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <div>
              <label className="block text-[11px] text-muted mb-1">{isRu ? 'Зона (напр. ai, store)' : 'Zone (e.g. ai)'}</label>
              <input
                type="text"
                placeholder="store"
                value={newTld}
                onChange={(e) => setNewTld(e.target.value)}
                required
                className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink"
              />
            </div>
            <div>
              <label className="block text-[11px] text-muted mb-1">{isRu ? 'Себестоимость ($)' : 'Cost ($)'}</label>
              <input
                type="number"
                step="0.01"
                placeholder="4.99"
                value={newCost}
                onChange={(e) => setNewCost(e.target.value)}
                className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink"
              />
            </div>
            <div>
              <label className="block text-[11px] text-muted mb-1">{isRu ? 'Наценка (%)' : 'Markup (%)'}</label>
              <input
                type="number"
                placeholder="30"
                value={newMarkup}
                onChange={(e) => setNewMarkup(e.target.value)}
                className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink"
              />
            </div>
            <div>
              <label className="block text-[11px] text-muted mb-1">{isRu ? 'Или Фикс (₽)' : 'Or Fixed (₽)'}</label>
              <input
                type="number"
                placeholder="590"
                value={newFixed}
                onChange={(e) => setNewFixed(e.target.value)}
                className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-xs text-ink"
              />
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="btn-ghost text-xs px-3 py-1.5"
            >
              {isRu ? 'Отмена' : 'Cancel'}
            </button>
            <button
              type="submit"
              disabled={adding}
              className="btn-primary text-xs px-3 py-1.5"
            >
              {adding ? <Spinner className="h-3 w-3" /> : null}
              {isRu ? 'Сохранить зону' : 'Save Zone'}
            </button>
          </div>
        </form>
      )}

      {/* Pricing Table */}
      {rows.length === 0 ? (
        <p className="p-4 text-center text-xs text-muted">
          {isRu ? 'Нет настроенных зон' : 'No zones configured'}
        </p>
      ) : (
        <Table
          head={[
            isRu ? 'Зона' : 'Zone',
            isRu ? 'Себестоимость $' : 'Cost $',
            isRu ? 'Наценка %' : 'Markup %',
            isRu ? 'Фикс ₽' : 'Fixed ₽',
            isRu ? 'Цена клиента ₽' : 'Client Price ₽',
            isRu ? 'В топе' : 'Featured',
            isRu ? 'Активен' : 'Enabled',
            '',
          ]}
          rows={rows.map((row) => [
            <span key="z" className="font-semibold text-ink">
              .{row.tld}
            </span>,
            <EditableNumber
              key="c"
              value={row.register_cost_usd}
              step="0.01"
              prefix="$"
              onSave={(value) => save(row.tld, { register_cost_usd: value })}
            />,
            <EditableNumber
              key="m"
              value={row.markup_percent}
              placeholder="Auto"
              suffix="%"
              onSave={(value) => save(row.tld, { markup_percent: value })}
            />,
            <EditableNumber
              key="f"
              value={row.fixed_price_rub}
              placeholder="—"
              suffix="₽"
              onSave={(value) => save(row.tld, { fixed_price_rub: value })}
            />,
            <span key="ret" className="font-semibold tabular-nums text-ok">
              {row.retail_price_rub !== undefined && row.retail_price_rub !== null
                ? `${Number(row.retail_price_rub).toLocaleString('ru-RU')} ₽`
                : '—'}
            </span>,
            <button
              key="feat"
              type="button"
              title={isRu ? 'Показывать в чипсах поиска' : 'Show in search chips'}
              onClick={() => save(row.tld, { is_featured: !row.is_featured })}
              className={`badge cursor-pointer ${
                row.is_featured ? 'bg-warn/15 text-warn' : 'bg-muted/15 text-muted'
              }`}
            >
              {row.is_featured ? '★ ' + (isRu ? 'Топ' : 'Top') : '—'}
            </button>,
            <button
              key="e"
              type="button"
              onClick={() => save(row.tld, { is_enabled: !row.is_enabled })}
              className={`badge cursor-pointer ${
                row.is_enabled ? 'bg-ok/15 text-ok' : 'bg-muted/15 text-muted'
              }`}
            >
              {row.is_enabled ? (isRu ? 'Вкл' : 'on') : (isRu ? 'Выкл' : 'off')}
            </button>,
            <div key="act" className="flex items-center gap-1.5">
              {savingTld === row.tld ? (
                <Spinner className="h-3 w-3" />
              ) : (
                <button
                  type="button"
                  onClick={() => removeTld(row.tld)}
                  title={isRu ? 'Удалить зону' : 'Delete'}
                  className="text-muted hover:text-danger text-xs px-1.5 py-0.5 rounded cursor-pointer"
                >
                  ✕
                </button>
              )}
            </div>,
          ])}
        />
      )}
    </div>
  );
}

function EditableNumber({ value, placeholder = '', prefix = '', suffix = '', onSave }) {
  const [draft, setDraft] = useState(value ?? '');

  useEffect(() => {
    setDraft(value ?? '');
  }, [value]);

  return (
    <div className="inline-flex items-center gap-0.5">
      {prefix ? <span className="text-muted text-[11px]">{prefix}</span> : null}
      <input
        type="text"
        placeholder={placeholder}
        className="w-20 rounded-md border border-line bg-surface px-2 py-1 text-xs tabular-nums text-ink focus:border-accent focus:outline-none"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => {
          if (draft === '') {
            if (value !== null && value !== undefined) onSave(null);
            return;
          }
          const next = Number(draft);
          if (Number.isNaN(next)) {
            setDraft(value ?? '');
            return;
          }
          if (next !== value) onSave(next);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.currentTarget.blur();
        }}
      />
      {suffix ? <span className="text-muted text-[11px]">{suffix}</span> : null}
    </div>
  );
}

// ------------------------------------------------------------------ categories

function CategoriesView({ language }) {
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState(null);
  const [error, setError] = useState(null);
  const [tldInputs, setTldInputs] = useState({});
  const t = makeTranslator(language);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .adminCategories()
      .then((res) => {
        setCategories(res?.categories || []);
      })
      .catch((err) => {
        setError(err?.message || 'Could not load categories.');
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const moveCategory = (index, dir) => {
    const target = index + dir;
    if (target < 0 || target >= categories.length) return;
    const next = [...categories];
    const [moved] = next.splice(index, 1);
    next.splice(target, 0, moved);
    setCategories(next);
  };

  const updateCategory = (index, patch) => {
    const next = [...categories];
    next[index] = { ...next[index], ...patch };
    setCategories(next);
  };

  const removeTld = (catIndex, tldToRemove) => {
    const cat = categories[catIndex];
    const tlds = (cat.tlds || []).filter((item) => item !== tldToRemove);
    updateCategory(catIndex, { tlds });
  };

  const handleAddTld = (catIndex) => {
    const inputVal = (tldInputs[catIndex] || '').trim().toLowerCase().replace(/^\.+/, '');
    if (!inputVal) return;
    const cat = categories[catIndex];
    const current = cat.tlds || [];
    if (!current.includes(inputVal)) {
      updateCategory(catIndex, { tlds: [...current, inputVal] });
    }
    setTldInputs((prev) => ({ ...prev, [catIndex]: '' }));
  };

  const handleAddCategory = () => {
    const id = `cat_${Date.now()}`;
    const newCat = {
      id,
      name: language === 'ru' ? 'Новая категория' : 'New Category',
      name_en: 'New Category',
      icon: '🏷️',
      sort_order: categories.length + 1,
      is_enabled: true,
      rule_type: 'custom_tlds',
      rule_value: null,
      tlds: ['com', 'net', 'org'],
    };
    setCategories([...categories, newCat]);
  };

  const handleDeleteCategory = (index) => {
    if (window.confirm(`${t('admin.cat.confirm_delete')} "${categories[index].name}"?`)) {
      setCategories(categories.filter((_, i) => i !== index));
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await api.updateAdminCategories(categories);
      setCategories(res?.categories || categories);
      setSavedNote(t('admin.cat.save_success'));
      setTimeout(() => setSavedNote(null), 3000);
    } catch (err) {
      setError(err?.message || 'Could not save categories.');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (window.confirm(`${t('admin.cat.reset_btn')}?`)) {
      setLoading(true);
      setError(null);
      try {
        const res = await api.resetAdminCategories();
        setCategories(res?.categories || []);
        setSavedNote(t('admin.cat.save_success'));
        setTimeout(() => setSavedNote(null), 3000);
      } catch (err) {
        setError(err?.message || 'Could not reset categories.');
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-base font-semibold text-ink">{t('admin.cat.title')}</h2>
          <p className="text-xs text-muted">{t('admin.cat.subtitle')}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleAddCategory}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-ink hover:border-accent transition-colors cursor-pointer"
          >
            {t('admin.cat.add_btn')}
          </button>
          <button
            type="button"
            onClick={handleReset}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-muted hover:text-danger hover:border-danger transition-colors cursor-pointer"
          >
            {t('admin.cat.reset_btn')}
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="rounded-lg bg-accent px-4 py-1.5 text-xs font-bold text-white shadow-xs hover:bg-accent-hover transition-colors disabled:opacity-50 cursor-pointer"
          >
            {saving ? '...' : t('admin.cat.save_btn')}
          </button>
        </div>
      </div>

      {savedNote && (
        <div className="rounded-lg border border-accent/40 bg-accent/10 px-4 py-2.5 text-xs font-medium text-accent">
          ✓ {savedNote}
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-2.5 text-xs font-medium text-danger">
          ✕ {error}
        </div>
      )}

      {loading ? (
        <div className="rounded-xl border border-line bg-surface p-8 text-center text-xs text-muted">
          Загрузка категорий...
        </div>
      ) : categories.length === 0 ? (
        <div className="rounded-xl border border-line bg-surface p-8 text-center text-xs text-muted">
          Нет настроенных категорий. Нажмите «Сбросить к стандарту» или добавьте новую.
        </div>
      ) : (
        <div className="space-y-4">
          {categories.map((cat, idx) => (
            <div
              key={cat.id || idx}
              className={`rounded-xl border p-4 transition-all ${
                cat.is_enabled
                  ? 'border-line bg-surface shadow-xs'
                  : 'border-line/40 bg-surface/40 opacity-70'
              }`}
            >
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                {/* Left: Reorder & Status & Icon & Title */}
                <div className="flex items-start gap-3 flex-1 min-w-0">
                  {/* Reorder arrows */}
                  <div className="flex flex-col gap-1 pt-0.5">
                    <button
                      type="button"
                      disabled={idx === 0}
                      onClick={() => moveCategory(idx, -1)}
                      className="rounded border border-line bg-card px-1.5 py-0.5 text-[10px] text-muted hover:text-ink disabled:opacity-30 cursor-pointer"
                      title="Выше"
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      disabled={idx === categories.length - 1}
                      onClick={() => moveCategory(idx, 1)}
                      className="rounded border border-line bg-card px-1.5 py-0.5 text-[10px] text-muted hover:text-ink disabled:opacity-30 cursor-pointer"
                      title="Ниже"
                    >
                      ▼
                    </button>
                  </div>

                  {/* Icon */}
                  <div className="w-12 shrink-0">
                    <label className="text-[10px] uppercase font-bold text-muted block mb-0.5">
                      Иконка
                    </label>
                    <input
                      type="text"
                      className="w-full text-center text-lg rounded-md border border-line bg-card px-1 py-1 text-ink focus:border-accent focus:outline-none"
                      value={cat.icon || ''}
                      onChange={(e) => updateCategory(idx, { icon: e.target.value })}
                    />
                  </div>

                  {/* RU / EN Names */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 flex-1 min-w-0">
                    <div>
                      <label className="text-[10px] uppercase font-bold text-muted block mb-0.5">
                        Название (RU)
                      </label>
                      <input
                        type="text"
                        className="w-full rounded-md border border-line bg-card px-2.5 py-1 text-xs font-semibold text-ink focus:border-accent focus:outline-none"
                        value={cat.name || ''}
                        onChange={(e) => updateCategory(idx, { name: e.target.value })}
                      />
                    </div>
                    <div>
                      <label className="text-[10px] uppercase font-bold text-muted block mb-0.5">
                        Название (EN)
                      </label>
                      <input
                        type="text"
                        className="w-full rounded-md border border-line bg-card px-2.5 py-1 text-xs text-ink focus:border-accent focus:outline-none"
                        value={cat.name_en || ''}
                        onChange={(e) => updateCategory(idx, { name_en: e.target.value })}
                      />
                    </div>
                  </div>
                </div>

                {/* Right: Enabled toggle & Delete */}
                <div className="flex items-center gap-2 self-end md:self-auto shrink-0">
                  <button
                    type="button"
                    onClick={() => updateCategory(idx, { is_enabled: !cat.is_enabled })}
                    className={`rounded-lg px-2.5 py-1 text-xs font-medium border transition-colors cursor-pointer ${
                      cat.is_enabled
                        ? 'border-accent/40 bg-accent/15 text-accent'
                        : 'border-line bg-card text-muted'
                    }`}
                  >
                    {cat.is_enabled ? `✓ ${t('admin.cat.status_active')}` : `✕ ${t('admin.cat.status_hidden')}`}
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDeleteCategory(idx)}
                    className="rounded-lg border border-line bg-card px-2 py-1 text-xs text-muted hover:border-danger hover:text-danger transition-colors cursor-pointer"
                    title={t('admin.cat.confirm_delete')}
                  >
                    🗑️
                  </button>
                </div>
              </div>

              {/* Rule Selector & Details */}
              <div className="mt-3 border-t border-line/40 pt-3 flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="text-xs font-medium text-muted">Тип правила:</span>
                  <select
                    className="rounded-md border border-line bg-card px-2 py-1 text-xs font-medium text-ink focus:border-accent focus:outline-none"
                    value={cat.rule_type || 'custom_tlds'}
                    onChange={(e) => updateCategory(idx, { rule_type: e.target.value })}
                  >
                    <option value="custom_tlds">{t('admin.cat.rule_custom')}</option>
                    <option value="price_max">{t('admin.cat.rule_price')}</option>
                    <option value="featured">{t('admin.cat.rule_featured')}</option>
                    <option value="all">{t('admin.cat.rule_all')}</option>
                  </select>

                  {cat.rule_type === 'price_max' && (
                    <div className="flex items-center gap-1.5 text-xs text-muted">
                      <span>до</span>
                      <input
                        type="number"
                        className="w-20 rounded-md border border-line bg-card px-2 py-1 text-xs font-bold text-ink focus:border-accent focus:outline-none"
                        value={cat.rule_value ?? 700}
                        onChange={(e) => updateCategory(idx, { rule_value: Number(e.target.value) })}
                      />
                      <span>₽ (динамический отбор зон с ценой ниже этого порога)</span>
                    </div>
                  )}

                  {cat.rule_type === 'featured' && (
                    <span className="text-xs text-muted">
                      (автоматически включает все зоны с отметкой ★ Топ в ценах TLD)
                    </span>
                  )}
                  {cat.rule_type === 'all' && (
                    <span className="text-xs text-muted">
                      (все 800+ зон с постраничной загрузкой)
                    </span>
                  )}
                </div>

                {/* TLD List (for custom_tlds or fallback) */}
                {(cat.rule_type === 'custom_tlds' || cat.rule_type === 'price_max') && (
                  <div className="mt-1">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[11px] font-semibold text-muted">
                        {cat.rule_type === 'price_max' ? 'Резервный список зон:' : 'Список зон в категории:'}
                      </span>
                      <span className="text-[11px] text-muted">{(cat.tlds || []).length} зон</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5 p-2 rounded-lg border border-line bg-card/60">
                      {(cat.tlds || []).map((tld) => (
                        <span
                          key={tld}
                          className="inline-flex items-center gap-1 rounded bg-elevated border border-line px-2 py-0.5 text-xs font-mono font-medium text-ink"
                        >
                          .{tld}
                          <button
                            type="button"
                            onClick={() => removeTld(idx, tld)}
                            className="text-muted hover:text-danger ml-0.5 text-[10px] cursor-pointer"
                            title="Удалить"
                          >
                            ✕
                          </button>
                        </span>
                      ))}
                      <div className="inline-flex items-center gap-1">
                        <input
                          type="text"
                          placeholder="зона (.ai)"
                          className="w-20 rounded border border-line bg-surface px-2 py-0.5 text-xs font-mono text-ink focus:border-accent focus:outline-none"
                          value={tldInputs[idx] || ''}
                          onChange={(e) => setTldInputs({ ...tldInputs, [idx]: e.target.value })}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              handleAddTld(idx);
                            }
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => handleAddTld(idx)}
                          className="rounded bg-elevated border border-line px-2 py-0.5 text-xs font-medium text-muted hover:text-ink cursor-pointer"
                        >
                          +
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
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
        <span
          key="p"
          className="max-w-xs truncate font-mono text-xs text-muted"
          title={log.endpoint || log.path}
        >
          {log.endpoint || log.path}
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
          {Math.round(log.duration_ms || 0)}
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

  // AI & Suggestions (OmniRoute)
  const [omnirouteUrl, setOmnirouteUrl] = useState('http://main_omniroute:20128/v1');
  const [omnirouteModel, setOmnirouteModel] = useState('fast');
  const [omnirouteKey, setOmnirouteKey] = useState('');

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
        setOmnirouteUrl(cfg.omniroute_base_url || 'http://main_omniroute:20128/v1');
        setOmnirouteModel(cfg.omniroute_model || 'fast');
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
      omniroute_base_url: omnirouteUrl.trim() || undefined,
      omniroute_model: omnirouteModel.trim() || undefined,
      registration_worker_enabled: workerEnabled,
    };

    if (dynadotKey.trim()) payload.dynadot_api_key = dynadotKey.trim();
    if (dynadotSecret.trim()) payload.dynadot_api_secret = dynadotSecret.trim();
    if (omnirouteKey.trim()) payload.omniroute_api_key = omnirouteKey.trim();
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
      setOmnirouteKey('');
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

        {/* 6. AI & Smart Domain Suggestions (OmniRoute) */}
        <div className="card space-y-4 p-5">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">
              {isRu ? '🤖 ИИ-подбор доменов (OmniRoute)' : '🤖 AI Domain Suggestions (OmniRoute)'}
            </h3>
            <KeyBadge configured={data?.ai_configured} language={language} />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-ink">
                Base URL
              </label>
              <input
                type="text"
                placeholder="http://main_omniroute:20128/v1"
                value={omnirouteUrl}
                onChange={(e) => setOmnirouteUrl(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>

            <div>
              <label className="mb-1 block text-xs font-medium text-ink">
                {isRu ? 'Модель ИИ' : 'AI Model'}
              </label>
              <input
                type="text"
                placeholder="fast"
                value={omnirouteModel}
                onChange={(e) => setOmnirouteModel(e.target.value)}
                className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
              />
            </div>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="text-xs font-medium text-ink">
                {isRu ? 'OmniRoute API-ключ' : 'OmniRoute API Key'}
              </label>
              <KeyBadge configured={data?.has_omniroute_api_key} language={language} />
            </div>
            <input
              type="password"
              placeholder={data?.has_omniroute_api_key ? '•••••••••••• (ключ сохранён в базе)' : 'Bearer API-ключ OmniRoute'}
              value={omnirouteKey}
              onChange={(e) => setOmnirouteKey(e.target.value)}
              className="w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm font-mono text-ink focus:border-accent focus:outline-none"
            />
          </div>

          <TestConnectionButton provider="ai" label="OmniRoute AI" language={language} />
        </div>

        {/* 7. Worker & System */}
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
