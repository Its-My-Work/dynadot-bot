import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

// This jsdom build exposes no `localStorage` (node warns that it is
// unavailable without --localstorage-file). The app guards every access so it
// degrades gracefully in production, but the theme-persistence tests need
// somewhere real to write — provide a minimal in-memory equivalent.
if (typeof window !== 'undefined' && !window.localStorage) {
  const store = new Map();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    writable: true,
    value: {
      getItem: (key) => (store.has(String(key)) ? store.get(String(key)) : null),
      setItem: (key, value) => store.set(String(key), String(value)),
      removeItem: (key) => store.delete(String(key)),
      clear: () => store.clear(),
      key: (index) => [...store.keys()][index] ?? null,
      get length() {
        return store.size;
      },
    },
  });
}

if (typeof window !== 'undefined' && window.localStorage) {
  window.localStorage.setItem('dynadot_admin_token', 'test_token');
  window.localStorage.setItem('dynadot_admin_lang', 'en');
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
