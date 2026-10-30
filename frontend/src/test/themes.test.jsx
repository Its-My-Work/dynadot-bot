/** Theme registry, persistence, and the picker in both apps. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import AdminApp from '../AdminApp';
import api from '../lib/api';
import { ThemePicker } from '../components/ui';
import {
  DEFAULT_LIGHT_THEME,
  DEFAULT_THEME,
  THEMES,
  THEME_IDS,
  applyThemeAttribute,
  initialTheme,
  isTheme,
  normalizeTheme,
  storeTheme,
  storedTheme,
  themeMeta,
} from '../lib/themes';
import { applyTheme } from '../lib/telegram';

const TELEGRAM_ID = 424242;

beforeEach(() => {
  window.localStorage.clear();
  delete window.Telegram;
  delete document.documentElement.dataset.theme;
  document.documentElement.style.colorScheme = '';
});

describe('theme registry', () => {
  it('ships exactly the six commercial themes (3 dark, 3 light)', () => {
    expect(THEME_IDS).toEqual([
      'onyx-neon',
      'linear-indigo',
      'sunset-crimson',
      'nordic-mist',
      'emerald-spring',
      'solar-amber',
    ]);
  });

  it('gives every theme a name, hint, accent and scheme', () => {
    THEMES.forEach((theme) => {
      expect(theme.name).toBeTruthy();
      expect(theme.hint).toBeTruthy();
      expect(theme.accent).toMatch(/^#[0-9a-f]{6}$/i);
      expect(['light', 'dark']).toContain(theme.scheme);
    });
  });

  it('defines a complete CSS block for each theme id', async () => {
    // Guards the three-list invariant: a theme in JS with no CSS block renders
    // as the default with no error anywhere.
    const fs = await import('node:fs/promises');
    // Vitest serves modules over http, so `import.meta.url` is not a file URL;
    // resolve from the project root (vitest's cwd) instead.
    const css = await fs.readFile('src/index.css', 'utf8');
    const required = [
      '--c-bg',
      '--c-surface',
      '--c-elevated',
      '--c-line',
      '--c-ink',
      '--c-muted',
      '--c-faint',
      '--c-accent',
      '--c-accent-ink',
      '--c-ok',
      '--c-warn',
      '--c-danger',
    ];
    THEME_IDS.forEach((id) => {
      const match = css.match(
        new RegExp(`\\[data-theme='${id}'\\]\\s*\\{([^}]*)\\}`),
      );
      expect(match, `missing CSS block for ${id}`).toBeTruthy();
      required.forEach((variable) => {
        expect(match[1], `${id} is missing ${variable}`).toContain(variable);
      });
    });
  });
});

describe('normalizeTheme', () => {
  it('accepts known ids and rejects everything else', () => {
    expect(isTheme('sunset-crimson')).toBe(true);
    expect(isTheme('nope')).toBe(false);
    expect(normalizeTheme('emerald-spring')).toBe('emerald-spring');
    expect(normalizeTheme('removed-theme')).toBe(DEFAULT_THEME);
    expect(normalizeTheme(undefined)).toBe(DEFAULT_THEME);
    expect(normalizeTheme(null, 'nordic-mist')).toBe('nordic-mist');
  });

  it('resolves metadata for unknown ids instead of throwing', () => {
    expect(themeMeta('linear-indigo').name).toBe('Linear Indigo');
    expect(themeMeta('garbage').id).toBe(DEFAULT_THEME);
  });
});

describe('persistence', () => {
  it('round-trips through localStorage', () => {
    expect(storedTheme()).toBeNull();
    storeTheme('sunset-crimson');
    expect(storedTheme()).toBe('sunset-crimson');
  });

  it('ignores a stale id left by a removed theme', () => {
    window.localStorage.setItem('dynadot.theme', 'retired-theme');
    expect(storedTheme()).toBeNull();
    expect(initialTheme()).toBe(DEFAULT_THEME);
  });

  it('survives localStorage throwing (private mode, embedded webviews)', () => {
    const spy = vi
      .spyOn(window.localStorage, 'setItem')
      .mockImplementation(() => {
        throw new Error('QuotaExceededError');
      });
    expect(() => storeTheme('emerald-spring')).not.toThrow();
    spy.mockRestore();
  });
});

describe('applyThemeAttribute', () => {
  it('sets data-theme and the native colour-scheme hint', () => {
    applyThemeAttribute('nordic-mist');
    expect(document.documentElement.dataset.theme).toBe('nordic-mist');
    expect(document.documentElement.style.colorScheme).toBe('light');

    applyThemeAttribute('sunset-crimson');
    expect(document.documentElement.dataset.theme).toBe('sunset-crimson');
    expect(document.documentElement.style.colorScheme).toBe('dark');
  });
});

describe('initialTheme', () => {
  it('prefers the saved choice over the client colour scheme', () => {
    storeTheme('emerald-spring');
    expect(initialTheme('light')).toBe('emerald-spring');
  });

  it('falls back to the light theme on a light client', () => {
    expect(initialTheme('light')).toBe(DEFAULT_LIGHT_THEME);
    expect(initialTheme('dark')).toBe(DEFAULT_THEME);
    expect(initialTheme()).toBe(DEFAULT_THEME);
  });
});

describe('applyTheme (Telegram bridge)', () => {
  it('honours an explicit choice over the client scheme', () => {
    window.Telegram = { WebApp: { colorScheme: 'light' } };
    storeTheme('sunset-crimson');
    // A themeChanged event must not discard what the user picked.
    expect(applyTheme()).toBe('sunset-crimson');
    expect(document.documentElement.dataset.theme).toBe('sunset-crimson');
  });

  it('uses the light theme for a light client with no saved choice', () => {
    window.Telegram = { WebApp: { colorScheme: 'light' } };
    expect(applyTheme()).toBe(DEFAULT_LIGHT_THEME);
  });
});

describe('ThemePicker', () => {
  it('opens, lists every theme, and reports the chosen id', async () => {
    const onSelect = vi.fn();
    render(<ThemePicker theme={DEFAULT_THEME} onSelect={onSelect} />);

    expect(screen.queryByTestId('theme-menu')).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId('theme-trigger'));

    const menu = screen.getByTestId('theme-menu');
    expect(menu.querySelectorAll('[data-theme-option]')).toHaveLength(
      THEME_IDS.length,
    );

    await userEvent.click(
      menu.querySelector('[data-theme-option="linear-indigo"]'),
    );
    expect(onSelect).toHaveBeenCalledWith('linear-indigo');
    // Picking closes the popover; leaving it open hides the result.
    expect(screen.queryByTestId('theme-menu')).not.toBeInTheDocument();
  });

  it('marks the active theme as selected', async () => {
    render(<ThemePicker theme="sunset-crimson" onSelect={() => {}} />);
    await userEvent.click(screen.getByTestId('theme-trigger'));
    const option = screen
      .getByTestId('theme-menu')
      .querySelector('[data-theme-option="sunset-crimson"]');
    expect(option).toHaveAttribute('aria-selected', 'true');
  });

  it('closes on Escape', async () => {
    render(<ThemePicker theme={DEFAULT_THEME} onSelect={() => {}} />);
    await userEvent.click(screen.getByTestId('theme-trigger'));
    expect(screen.getByTestId('theme-menu')).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByTestId('theme-menu')).not.toBeInTheDocument(),
    );
  });
});

describe('TMA theme switching', () => {
  beforeEach(() => {
    window.Telegram = {
      WebApp: {
        initData: 'x',
        initDataUnsafe: { user: { id: TELEGRAM_ID, language_code: 'ru' } },
        colorScheme: 'dark',
        ready: () => {},
        expand: () => {},
      },
    };
    vi.spyOn(api, 'zones').mockResolvedValue([]);
  });

  it('applies and persists the picked theme', async () => {
    vi.spyOn(api, 'getUser').mockResolvedValue({
      language: 'ru',
      theme: 'onyx-neon',
    });
    const setTheme = vi.spyOn(api, 'setUserTheme').mockResolvedValue({});

    render(<App />);
    await userEvent.click(await screen.findByTestId('theme-trigger'));
    await userEvent.click(
      screen.getByTestId('theme-menu').querySelector('[data-theme-option="emerald-spring"]'),
    );

    expect(document.documentElement.dataset.theme).toBe('emerald-spring');
    expect(window.localStorage.getItem('dynadot.theme')).toBe('emerald-spring');
    await waitFor(() =>
      expect(setTheme).toHaveBeenCalledWith(TELEGRAM_ID, 'emerald-spring'),
    );
  });

  it('adopts the theme stored on the profile', async () => {
    vi.spyOn(api, 'getUser').mockResolvedValue({
      language: 'ru',
      theme: 'nordic-mist',
    });
    render(<App />);
    await waitFor(() =>
      expect(document.documentElement.dataset.theme).toBe('nordic-mist'),
    );
  });

  it('keeps the UI usable when persisting the theme fails', async () => {
    vi.spyOn(api, 'getUser').mockResolvedValue({ language: 'ru', theme: 'onyx-neon' });
    vi.spyOn(api, 'setUserTheme').mockRejectedValue(new Error('offline'));

    render(<App />);
    await userEvent.click(await screen.findByTestId('theme-trigger'));
    await userEvent.click(
      screen.getByTestId('theme-menu').querySelector('[data-theme-option="sunset-crimson"]'),
    );

    // Optimistic: a failed PATCH must not revert what the user sees.
    expect(document.documentElement.dataset.theme).toBe('sunset-crimson');
    expect(window.localStorage.getItem('dynadot.theme')).toBe('sunset-crimson');
  });

  it('does not let a late profile response override a fresh choice', async () => {
    let resolveUser;
    vi.spyOn(api, 'getUser').mockReturnValue(
      new Promise((resolve) => {
        resolveUser = resolve;
      }),
    );
    vi.spyOn(api, 'setUserTheme').mockResolvedValue({});

    render(<App />);
    await userEvent.click(await screen.findByTestId('theme-trigger'));
    await userEvent.click(
      screen.getByTestId('theme-menu').querySelector('[data-theme-option="emerald-spring"]'),
    );

    resolveUser({ language: 'ru', theme: 'linear-indigo' });
    await waitFor(() =>
      expect(document.documentElement.dataset.theme).toBe('emerald-spring'),
    );
  });
});

describe('admin theme switching', () => {
  it('switches locally without touching the user API', async () => {
    vi.spyOn(api, 'adminStats').mockResolvedValue({ refunds_pending: 0 });
    const getUser = vi.spyOn(api, 'getUser');
    const setTheme = vi.spyOn(api, 'setUserTheme');

    render(<AdminApp />);
    await userEvent.click(await screen.findByTestId('theme-trigger'));
    await userEvent.click(
      screen.getByTestId('theme-menu').querySelector('[data-theme-option="linear-indigo"]'),
    );

    expect(document.documentElement.dataset.theme).toBe('linear-indigo');
    expect(window.localStorage.getItem('dynadot.theme')).toBe('linear-indigo');
    // The console has no telegram id, so profile calls would be meaningless.
    expect(getUser).not.toHaveBeenCalled();
    expect(setTheme).not.toHaveBeenCalled();
  });
});
