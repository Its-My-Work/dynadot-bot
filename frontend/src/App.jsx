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
import { AppBar, Brand, EmptyState, LangToggle } from './components/ui';

const TABS = [
  { id: 'search', labelKey: 'nav.search', icon: '⌕' },
  { id: 'domains', labelKey: 'nav.domains', icon: '◇' },
  { id: 'orders', labelKey: 'nav.orders', icon: '▤' },
];

export default function App() {
  const [language, setLang] = useState(() => normalizeLanguage(initialLanguage()));
  const [tab, setTab] = useState('search');
  const [candidate, setCandidate] = useState(null);
  const [telegramId] = useState(() => resolveTelegramId());

  const t = makeTranslator(language);

  useEffect(() => {
    setLanguage(language);
  }, [language]);

  // The saved profile preference wins over Telegram's client locale: the user
  // chose it explicitly in the bot.
  useEffect(() => {
    if (!telegramId) return;
    api
      .getUser(telegramId)
      .then((user) => {
        if (user?.language) setLang(normalizeLanguage(user.language));
      })
      .catch(() => {
        // A brand-new user has no profile yet; the client locale is fine.
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

  if (!telegramId) {
    return (
      <div className="mx-auto max-w-md px-4">
        <EmptyState icon="⚠" title={t('common.no_user')} />
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-full max-w-md flex-col">
      <AppBar>
        <div className="mx-auto flex h-full max-w-md items-center justify-between px-4">
          <Brand subtitle={t('app.subtitle')} />
          <LangToggle language={language} onToggle={switchLanguage} />
        </div>
      </AppBar>

      <main className="flex-1 px-4 pb-24">
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
            bg-bg/95 backdrop-blur"
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
