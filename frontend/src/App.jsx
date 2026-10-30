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
  initialLanguage,
  resolveTelegramId,
  setBackButton,
} from './lib/telegram';
import CheckoutScreen from './screens/CheckoutScreen';
import DomainsScreen from './screens/DomainsScreen';
import OrdersScreen from './screens/OrdersScreen';
import SearchScreen from './screens/SearchScreen';
import VpsScreen from './screens/VpsScreen';
import { AppBar, Brand, LangToggle, ThemePicker } from './components/ui';
import useTheme from './hooks/useTheme';

const TABS = [
  { id: 'search', labelKey: 'nav.search', icon: '⌕' },
  { id: 'vps', labelKey: 'nav.vps', icon: '⚡' },
  { id: 'domains', labelKey: 'nav.domains', icon: '◇' },
  { id: 'orders', labelKey: 'nav.orders', icon: '▤' },
];

export default function App() {
  const [language, setLang] = useState(() => normalizeLanguage(initialLanguage()));
  const [tab, setTab] = useState('search');
  const [candidate, setCandidate] = useState(null);
  const [telegramId] = useState(() => resolveTelegramId() || 9000000001);
  const { theme, selectTheme } = useTheme({ telegramId });

  const t = makeTranslator(language);

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
        api.getUser(telegramId).catch(() => {});
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
    <div className="mx-auto flex min-h-screen w-full max-w-md md:max-w-3xl flex-col">
      <AppBar>
        <div className="mx-auto flex h-full w-full max-w-md md:max-w-3xl items-center justify-between px-4">
          <div className="flex items-center gap-6">
            <Brand subtitle={t('app.subtitle')} />
            {/* Desktop Navigation inline in header */}
            <div className="hidden md:flex items-center gap-1">
              {TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setTab(item.id)}
                  aria-current={tab === item.id ? 'page' : undefined}
                  className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    tab === item.id
                      ? 'bg-elevated text-ink shadow-sm'
                      : 'text-muted hover:text-ink'
                  }`}
                >
                  <span aria-hidden="true" className="text-sm">
                    {item.icon}
                  </span>
                  <span>{t(item.labelKey)}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <ThemePicker
              theme={theme}
              onSelect={selectTheme}
              label={t('theme.label')}
            />
            <LangToggle language={language} onToggle={switchLanguage} />
          </div>
        </div>
      </AppBar>

      <main className="flex-1 px-4 pb-24 md:pb-12 pt-4">
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
              <DomainsScreen t={t} telegramId={telegramId} />
            ) : null}
            {tab === 'orders' ? (
              <OrdersScreen t={t} telegramId={telegramId} />
            ) : null}
          </>
        )}
      </main>

      {candidate ? null : (
        <nav
          className="fixed inset-x-0 bottom-0 z-40 border-t border-line
            bg-bg/95 backdrop-blur md:hidden"
        >
          <div className="mx-auto flex max-w-md">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                aria-current={tab === item.id ? 'page' : undefined}
                className={`flex flex-1 flex-col items-center gap-0.5 py-3 text-xs
                  transition-colors ${
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
  );
}
