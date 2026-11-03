/** TMA shell: three tabs plus a checkout overlay.
 *
 * Deliberately no router. The whole app is three lists and one flow, and a
 * router would add history states that fight Telegram's own back button —
 * which is wired directly to the checkout overlay instead.
 */
import { useCallback, useEffect, useState } from 'react';

import api, { setLanguage } from './lib/api';
import { makeTranslator, normalizeLanguage } from './lib/i18n';
import {
  getSessionUser,
  initialLanguage,
  resolveTelegramId,
  setBackButton,
} from './lib/telegram';
import AuthModal from './components/AuthModal';
import CheckoutScreen from './screens/CheckoutScreen';
import DomainsScreen from './screens/DomainsScreen';
import OrdersScreen from './screens/OrdersScreen';
import ProfileScreen from './screens/ProfileScreen';
import SearchScreen from './screens/SearchScreen';
import VpsScreen from './screens/VpsScreen';
import { AppBar, Brand, LangToggle, ThemePicker } from './components/ui';
import useTheme from './hooks/useTheme';

const TABS = [
  { id: 'search', labelKey: 'nav.search', icon: '🌐' },
  { id: 'vps', labelKey: 'nav.vps', icon: '⚡' },
  { id: 'domains', labelKey: 'nav.domains', icon: '◇' },
  { id: 'orders', labelKey: 'nav.orders', icon: '▤' },
  { id: 'profile', labelKey: 'nav.profile', icon: '👤' },
];

export default function App() {
  const [language, setLang] = useState(() => normalizeLanguage(initialLanguage()));
  const [tab, setTab] = useState(() => {
    try {
      const saved = window.localStorage.getItem('dynadot_active_tab');
      return saved && ['search', 'vps', 'domains', 'orders', 'profile'].includes(saved) ? saved : 'search';
    } catch {
      return 'search';
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem('dynadot_active_tab', tab);
    } catch {
      // Ignored
    }
  }, [tab]);
  const [candidate, setCandidate] = useState(null);
  const [currentUser, setCurrentUser] = useState(() => getSessionUser());
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [telegramId, setTelegramId] = useState(() => resolveTelegramId() || 9000000001);
  const { theme, selectTheme } = useTheme({ telegramId });

  const t = makeTranslator(language);

  useEffect(() => {
    function onAuthChanged(e) {
      const u = e.detail;
      setCurrentUser(u);
      if (u?.telegram_id) {
        setTelegramId(u.telegram_id);
      } else {
        setTelegramId(resolveTelegramId() || 9000000001);
      }
    }
    window.addEventListener('dynadot_auth_changed', onAuthChanged);
    return () => window.removeEventListener('dynadot_auth_changed', onAuthChanged);
  }, []);

  useEffect(() => {
    setLanguage(language);
  }, [language]);

  // The saved profile preference wins over Telegram's client locale: the user
  // chose it explicitly in the bot.
  useEffect(() => {
    if (!telegramId) return;
    api
      .ensureUser(telegramId)
      .then((user) => {
        if (user?.language) setLang(normalizeLanguage(user.language));
      })
      .catch(() => {
        // Fallback to getUser
        api
          .getUser(telegramId)
          .then((user) => {
            if (user?.language) setLang(normalizeLanguage(user.language));
          })
          .catch(() => {});
      });
  }, [telegramId]);

  const closeCheckout = useCallback(() => setCandidate(null), []);

  // Telegram's native back button drives the overlay, so the TMA needs no
  // back chrome of its own.
  useEffect(() => {
    if (!candidate) return setBackButton(null);
    return setBackButton(closeCheckout);
  }, [candidate, closeCheckout]);

  async function switchLanguage() {
    const next = language === 'ru' ? 'en' : 'ru';
    setLang(next);
    if (telegramId) {
      try {
        await api.setUserLanguage(telegramId, next);
      } catch {
        // Persisting the preference is best-effort; the UI already switched.
      }
    }
  }

  return (
    <div className="flex min-h-screen w-full bg-bg text-ink">
      {/* DESKTOP SIDEBAR (Visible on lg: >= 1024px when not in checkout) */}
      {!candidate ? (
        <aside className="hidden lg:flex w-60 border-r border-line bg-surface/80 flex-col justify-between shrink-0 select-none h-screen sticky top-0 z-30">
          <div>
            {/* Brand */}
            <div className="h-16 flex items-center justify-between px-5 border-b border-line">
              <div className="flex items-center gap-2.5">
                <div className="h-8 w-8 rounded-xl bg-accent flex items-center justify-center text-accent-ink font-bold text-sm shadow-xs">
                  D
                </div>
                <div>
                  <div className="font-semibold text-sm tracking-tight text-ink flex items-center gap-1.5">
                    <span>Dynadot</span>
                    <span className="text-[10px] bg-accent/15 text-accent border border-accent/25 font-medium px-1.5 py-0.5 rounded-md">
                      PRO
                    </span>
                  </div>
                  <div className="text-[10px] text-muted font-normal">{t('app.subtitle') || 'Cloud & Domains'}</div>
                </div>
              </div>
            </div>

            {/* Nav items */}
            <nav className="p-3 space-y-1 text-xs font-medium">
              {TABS.map((item) => {
                const active = tab === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => {
                      setTab(item.id);
                    }}
                    className={`w-full flex items-center justify-between px-3 py-2.5 rounded-xl transition-all cursor-pointer ${
                      active
                        ? 'bg-accent/15 text-accent font-semibold border border-accent/25 shadow-xs'
                        : 'text-muted hover:text-ink hover:bg-elevated/60 border border-transparent'
                    }`}
                  >
                    <div className="flex items-center gap-2.5">
                      <span className="text-sm">{item.icon}</span>
                      <span>{t(item.labelKey)}</span>
                    </div>
                  </button>
                );
              })}
            </nav>
          </div>

          {/* Sidebar Footer: Theme, Lang, Account */}
          <div className="p-3 border-t border-line space-y-2.5">
            <div className="flex items-center justify-between gap-2 px-1">
              <ThemePicker
                theme={theme}
                onSelect={selectTheme}
                label={t('theme.label')}
                testId="theme-trigger-sidebar"
                align="left"
                drop="up"
              />
              <LangToggle
                language={language}
                onToggle={switchLanguage}
                label="Switch language (sidebar)"
              />
            </div>

            <button
              type="button"
              onClick={() => setAuthModalOpen(true)}
              className="w-full flex items-center gap-2.5 p-2 rounded-xl bg-elevated/50 hover:bg-elevated border border-line text-left transition-colors cursor-pointer"
            >
              <div className="h-8 w-8 rounded-lg bg-surface border border-line flex items-center justify-center text-xs font-semibold text-ink shrink-0">
                {currentUser?.email ? currentUser.email.slice(0, 2).toUpperCase() : '👤'}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium text-ink truncate">
                  {currentUser?.email || (currentUser?.username ? `@${currentUser.username}` : (language === 'ru' ? 'Вход в аккаунт' : 'Login'))}
                </div>
                <div className="text-[10px] text-muted">
                  {currentUser?.role === 'admin' ? (language === 'ru' ? 'Администратор' : 'Admin') : (currentUser ? 'Пользователь' : (language === 'ru' ? 'Нажмите для входа' : 'Click to login'))}
                </div>
              </div>
            </button>
          </div>
        </aside>
      ) : null}

      {/* MAIN VIEWPORT */}
      <div className="flex-1 flex flex-col min-w-0 min-h-screen">
        {/* MOBILE APPBAR (hidden on lg:) */}
        <div className="lg:hidden">
          <AppBar>
            <div className="mx-auto flex h-full w-full max-w-md md:max-w-3xl items-center justify-between px-4">
              <Brand subtitle={t('app.subtitle')} />
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setAuthModalOpen(true)}
                  className="flex items-center gap-1.5 rounded-lg border border-card-border/80 bg-elevated/80 px-2.5 py-1.5 text-xs font-semibold text-ink hover:border-accent hover:text-accent transition-colors cursor-pointer"
                  title={currentUser?.email || (currentUser?.username ? `@${currentUser.username}` : (language === 'ru' ? 'Вход в аккаунт' : 'Login'))}
                >
                  <span>{currentUser?.email || currentUser?.username ? '👤' : '🔑'}</span>
                  <span className="hidden sm:inline max-w-[120px] truncate">
                    {currentUser?.email || (currentUser?.username ? `@${currentUser.username}` : (language === 'ru' ? 'Войти' : 'Login'))}
                  </span>
                </button>
                <ThemePicker
                  theme={theme}
                  onSelect={selectTheme}
                  label={t('theme.label')}
                />
                <LangToggle language={language} onToggle={switchLanguage} />
              </div>
            </div>
          </AppBar>
        </div>

        {/* DESKTOP HEADER (hidden on < lg) */}
        <div className="hidden lg:flex h-16 border-b border-line bg-surface/50 backdrop-blur-md px-8 items-center justify-between shrink-0 sticky top-0 z-20">
          <div className="flex items-center gap-3">
            <h1 className="text-base font-semibold tracking-tight text-ink">
              {candidate
                ? (language === 'ru' ? 'Оформление заказа' : 'Checkout')
                : tab === 'search'
                ? (language === 'ru' ? 'Поиск и регистрация доменов' : 'Domain Search & Registration')
                : tab === 'vps'
                ? (language === 'ru' ? 'Облачные VPS серверы' : 'Cloud VPS Servers')
                : tab === 'domains'
                ? (language === 'ru' ? 'Управление доменами' : 'Domain Management')
                : tab === 'orders'
                ? (language === 'ru' ? 'История заказов и счета' : 'Order History & Invoices')
                : (language === 'ru' ? 'Профиль и контактные данные' : 'Profile & WHOIS Contacts')}
            </h1>
          </div>

          <div className="flex items-center gap-3">
            {tab !== 'search' && !candidate && (
              <button
                type="button"
                onClick={() => setTab('search')}
                className="px-3.5 py-1.5 rounded-xl bg-accent text-accent-ink text-xs font-medium shadow-xs hover:opacity-90 transition-opacity flex items-center gap-1.5 cursor-pointer"
              >
                <span>+</span>
                <span>{language === 'ru' ? 'Зарегистрировать домен' : 'Register domain'}</span>
              </button>
            )}
          </div>
        </div>

        {/* MAIN CONTENT AREA */}
        <main className="flex-1 px-4 lg:px-8 pb-24 lg:pb-8 pt-4 lg:pt-6 max-w-md md:max-w-3xl lg:max-w-5xl w-full mx-auto">
          {candidate ? (
            <CheckoutScreen
              t={t}
              telegramId={telegramId}
              candidate={candidate}
              onDone={closeCheckout}
            />
          ) : (
            <>
              {tab === 'search' ? (
                <SearchScreen t={t} onSelect={setCandidate} />
              ) : null}
              {tab === 'vps' ? (
                <VpsScreen t={t} telegramId={telegramId} />
              ) : null}
              {tab === 'domains' ? (
                <DomainsScreen
                  t={t}
                  telegramId={telegramId}
                  onSelectCandidate={setCandidate}
                />
              ) : null}
              {tab === 'orders' ? (
                <OrdersScreen t={t} telegramId={telegramId} />
              ) : null}
              {tab === 'profile' ? (
                <ProfileScreen
                  t={t}
                  telegramId={telegramId}
                  language={language}
                  onLogout={() => setCurrentUser(null)}
                />
              ) : null}
            </>
          )}
        </main>

        {/* MOBILE NAVIGATION BAR (hidden on lg:) */}
        {candidate ? null : (
          <nav
            className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-bg/95 backdrop-blur lg:hidden"
          >
            <div className="mx-auto flex max-w-md">
              {TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setTab(item.id)}
                  aria-current={tab === item.id ? 'page' : undefined}
                  className={`flex flex-1 flex-col items-center gap-0.5 py-3 text-xs transition-colors ${
                    tab === item.id ? 'text-accent' : 'text-faint'
                  }`}
                >
                  <span aria-hidden="true" className="text-base">
                    {item.icon}
                  </span>
                  {t(item.labelKey)}
                </button>
              ))}
            </div>
          </nav>
        )}
      </div>

      <AuthModal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        onSuccess={(u) => setCurrentUser(u)}
      />
    </div>
  );
}
