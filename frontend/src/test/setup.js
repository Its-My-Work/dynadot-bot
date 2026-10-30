import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

const store = new Map();
const memoryStorage = {
  getItem: (key) => (store.has(String(key)) ? store.get(String(key)) : null),
  setItem: (key, value) => store.set(String(key), String(value)),
  removeItem: (key) => store.delete(String(key)),
  clear: () => store.clear(),
  key: (index) => [...store.keys()][index] ?? null,
  get length() {
    return store.size;
  },
};

if (typeof window !== 'undefined') {
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    writable: true,
    value: memoryStorage,
  });
  window.localStorage.setItem('dynadot_admin_token', 'test_token');
  window.localStorage.setItem('dynadot_admin_lang', 'en');
}
globalThis.localStorage = memoryStorage;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
