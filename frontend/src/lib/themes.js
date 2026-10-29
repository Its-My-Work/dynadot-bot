/** Design-theme registry and persistence.
 *
 * Three layers, in priority order, because each fixes a different failure:
 *
 * 1. **localStorage** is read synchronously at module load and applied before
 *    React mounts. Without it every launch flashes the default palette for as
 *    long as the profile request takes — the single most visible bug a
 *    server-only preference produces.
 * 2. **The user profile** (`GET /users/:id`) wins once it arrives, so the
 *    choice follows the account to a new device.
 * 3. **Telegram's colour scheme** is the fallback for a user who has never
 *    chosen: a light client gets the light theme rather than a black app.
 *
 * `THEMES` must stay in sync with the `[data-theme=...]` blocks in
 * `index.css` and with `Theme` in `app/models/enums.py`.
 */

export const THEMES = [
  {
    id: 'onyx-neon',
    name: 'Onyx Neon',
    hint: 'Near-black, electric blue',
    accent: '#5e86ff',
    scheme: 'dark',
  },
  {
    id: 'vercel-mono',
    name: 'Vercel Mono',
    hint: 'Pure greyscale, white accent',
    accent: '#ffffff',
    scheme: 'dark',
  },
  {
    id: 'linear-indigo',
    name: 'Linear Indigo',
    hint: 'Cool greys, violet accent',
    accent: '#8b7fff',
    scheme: 'dark',
  },
  {
    id: 'solana-cyber',
    name: 'Solana Cyber',
    hint: 'Deep purple, mint accent',
    accent: '#14f195',
    scheme: 'dark',
  },
  {
    id: 'nordic-mist',
    name: 'Nordic Mist',
    hint: 'Light, calm blue accent',
    accent: '#2f6fed',
    scheme: 'light',
  },
];

export const THEME_IDS = THEMES.map((theme) => theme.id);

export const DEFAULT_THEME = 'onyx-neon';

/** The theme used when the client is in light mode and nothing is saved. */
export const DEFAULT_LIGHT_THEME = 'nordic-mist';

const STORAGE_KEY = 'dynadot.theme';

export function isTheme(value) {
  return typeof value === 'string' && THEME_IDS.includes(value);
}

/** Coerce anything to a real theme id. Unknown values fall back, never throw:
 *  a stale localStorage entry from a removed theme must not break startup. */
export function normalizeTheme(value, fallback = DEFAULT_THEME) {
  return isTheme(value) ? value : fallback;
}

export function themeMeta(id) {
  return THEMES.find((theme) => theme.id === normalizeTheme(id)) ?? THEMES[0];
}

/** localStorage access is wrapped: it throws in private-mode Safari and in
 *  some embedded webviews, and a theme preference is never worth a crash. */
export function storedTheme() {
  try {
    const raw = window?.localStorage?.getItem(STORAGE_KEY);
    return isTheme(raw) ? raw : null;
  } catch {
    return null;
  }
}

export function storeTheme(id) {
  try {
    window?.localStorage?.setItem(STORAGE_KEY, normalizeTheme(id));
  } catch {
    // Non-persistent storage: the in-memory state still holds for this session.
  }
}

/** Write the theme onto <html data-theme>, which is what the CSS selects on. */
export function applyThemeAttribute(id) {
  const theme = normalizeTheme(id);
  if (typeof document !== 'undefined') {
    document.documentElement.dataset.theme = theme;
    // Lets the browser paint native form controls and scrollbars to match.
    document.documentElement.style.colorScheme = themeMeta(theme).scheme;
  }
  return theme;
}

/** Best theme to paint with *right now*, before any network call:
 *  saved choice, else the client's light/dark preference. */
export function initialTheme(colorScheme = null) {
  const saved = storedTheme();
  if (saved) return saved;
  return colorScheme === 'light' ? DEFAULT_LIGHT_THEME : DEFAULT_THEME;
}
