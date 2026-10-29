/** Shared theme state for the TMA and the admin panel.
 *
 * Owns the whole three-layer resolution described in `lib/themes.js` so both
 * apps behave identically and neither has to remember the ordering rules.
 *
 * Optimistic by design: `select()` paints and stores the theme immediately,
 * then persists to the profile. A theme switch that waits for a round-trip
 * feels broken, and a failed PATCH is not worth reverting a change the user
 * can plainly see — the localStorage copy keeps it across reloads anyway.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import api from '../lib/api';
import {
  applyThemeAttribute,
  initialTheme,
  normalizeTheme,
  storeTheme,
} from '../lib/themes';
import { telegramColorScheme } from '../lib/telegram';

export default function useTheme({ telegramId = null, persist = true } = {}) {
  // Resolve synchronously on first render: anything later is a visible flash.
  const [theme, setTheme] = useState(() =>
    applyThemeAttribute(initialTheme(telegramColorScheme())),
  );

  // Once the user has picked in this session, a late-arriving profile value
  // must not overwrite it — the request was already in flight when they tapped.
  const touched = useRef(false);

  useEffect(() => {
    applyThemeAttribute(theme);
  }, [theme]);

  useEffect(() => {
    if (!telegramId || !persist) return;
    let cancelled = false;
    api
      .getUser(telegramId)
      .then((user) => {
        if (cancelled || touched.current || !user?.theme) return;
        const next = normalizeTheme(user.theme, theme);
        setTheme(next);
        storeTheme(next);
      })
      .catch(() => {
        // No profile yet (or offline): the local choice is authoritative.
      });
    return () => {
      cancelled = true;
    };
    // `theme` is intentionally omitted: this syncs once per user, not on every
    // local switch, which would re-fetch and fight the optimistic update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [telegramId, persist]);

  const select = useCallback(
    (id) => {
      const next = normalizeTheme(id);
      touched.current = true;
      setTheme(next);
      storeTheme(next);
      if (telegramId && persist) {
        api.setUserTheme(telegramId, next).catch(() => {
          // Best-effort: localStorage already holds the choice.
        });
      }
      return next;
    },
    [telegramId, persist],
  );

  return { theme, selectTheme: select };
}
