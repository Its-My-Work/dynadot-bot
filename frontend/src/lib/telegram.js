/** Telegram WebApp bridge.
 *
 * Everything here degrades gracefully when `window.Telegram` is absent, because
 * the same bundle must run in a plain browser for development and tests. A
 * hard dependency on the SDK would make the app unopenable outside Telegram.
 */

export function webApp() {
  return typeof window !== 'undefined' ? window.Telegram?.WebApp ?? null : null;
}

export function isInsideTelegram() {
  return Boolean(webApp()?.initData);
}

/** The signed-in Telegram user, or null outside the client. */
export function telegramUser() {
  return webApp()?.initDataUnsafe?.user ?? null;
}

/** Resolve the telegram id, allowing a `?telegram_id=` override for local dev. */
export function resolveTelegramId() {
  const fromClient = telegramUser()?.id;
  if (fromClient) return fromClient;
  if (typeof window === 'undefined') return null;
  const override = new URLSearchParams(window.location.search).get('telegram_id');
  return override ? Number(override) : null;
}

/** Initial language: Telegram's own preference, else the saved profile. */
export function initialLanguage() {
  const code = telegramUser()?.language_code;
  return code && code.startsWith('ru') ? 'ru' : code ? 'en' : 'ru';
}

/** Apply Telegram's theme so the app never fights the surrounding client. */
export function applyTheme() {
  const app = webApp();
  if (typeof document === 'undefined') return 'dark';
  const scheme = app?.colorScheme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = scheme;
  return scheme;
}

export function ready() {
  const app = webApp();
  if (!app) return;
  app.ready();
  app.expand?.();
  applyTheme();
  app.onEvent?.('themeChanged', applyTheme);
}

/** Haptic confirmation on meaningful actions; silently ignored in a browser. */
export function haptic(type = 'light') {
  const haptics = webApp()?.HapticFeedback;
  if (!haptics) return;
  if (type === 'success' || type === 'error' || type === 'warning') {
    haptics.notificationOccurred?.(type);
  } else {
    haptics.impactOccurred?.(type);
  }
}

/** Open a payment URL.
 *
 * `openInvoice` only accepts Telegram invoice links, so a gateway URL must go
 * through `openLink`; falling back to `window.open` keeps dev usable.
 */
export function openPaymentUrl(url) {
  const app = webApp();
  if (app?.openLink) {
    app.openLink(url);
    return;
  }
  if (typeof window !== 'undefined') window.open(url, '_blank', 'noopener');
}

/** Native back button, so the TMA does not need its own chrome. */
export function setBackButton(handler) {
  const app = webApp();
  const back = app?.BackButton;
  if (!back) return () => {};
  if (handler) {
    back.show?.();
    back.onClick?.(handler);
    return () => {
      back.offClick?.(handler);
      back.hide?.();
    };
  }
  back.hide?.();
  return () => {};
}

export function closeApp() {
  webApp()?.close?.();
}
