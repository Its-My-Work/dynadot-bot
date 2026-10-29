/** Tests for i18n and formatting helpers.
 *
 * The RU/EN catalogues must stay in lockstep: a key present in only one
 * language silently renders the raw key to half the users.
 */
import { describe, expect, it } from 'vitest';

import catalogues, {
  LANGUAGES,
  formatDate,
  formatPrice,
  makeTranslator,
  normalizeLanguage,
  translate,
} from '../lib/i18n';

describe('catalogue integrity', () => {
  it('has identical key sets in ru and en', () => {
    const ru = Object.keys(catalogues.ru).sort();
    const en = Object.keys(catalogues.en).sort();
    expect(ru).toEqual(en);
  });

  it('has no empty strings', () => {
    LANGUAGES.forEach((language) => {
      const empty = Object.entries(catalogues[language])
        .filter(([, value]) => !String(value).trim())
        .map(([key]) => key);
      expect(empty).toEqual([]);
    });
  });

  it('uses the same placeholders in both languages', () => {
    const placeholders = (text) => (text.match(/\{(\w+)\}/g) ?? []).sort();
    const mismatched = Object.keys(catalogues.ru).filter(
      (key) =>
        placeholders(catalogues.ru[key]).join() !==
        placeholders(catalogues.en[key]).join(),
    );
    expect(mismatched).toEqual([]);
  });

  it('covers every order status the backend can return', () => {
    const statuses = [
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
    LANGUAGES.forEach((language) => {
      statuses.forEach((status) => {
        expect(translate(`status.${status}`, language)).not.toBe(
          `status.${status}`,
        );
      });
    });
  });
});

describe('translate', () => {
  it('returns the key itself when a string is missing', () => {
    // Louder than falling back to the other language, which hides the gap.
    expect(translate('nope.missing', 'ru')).toBe('nope.missing');
  });

  it('interpolates placeholders', () => {
    expect(translate('buy.pay', 'en', { price: '1 200' })).toBe('Pay 1 200 ₽');
  });

  it('leaves an unprovided placeholder untouched', () => {
    expect(translate('buy.pay', 'en')).toBe('Pay {price} ₽');
  });

  it('falls back to ru for an unsupported language', () => {
    expect(translate('search.available', 'de')).toBe('Свободен');
  });
});

describe('normalizeLanguage', () => {
  it.each([
    ['ru', 'ru'],
    ['en', 'en'],
    ['de', 'ru'],
    [undefined, 'ru'],
    [null, 'ru'],
  ])('maps %s to %s', (input, expected) => {
    expect(normalizeLanguage(input)).toBe(expected);
  });
});

describe('makeTranslator', () => {
  it('exposes its language so components can format numbers', () => {
    const t = makeTranslator('en');
    expect(t.language).toBe('en');
    expect(t('nav.search')).toBe('Search');
  });
});

describe('formatPrice', () => {
  it('drops kopecks, which are noise on domain prices', () => {
    expect(formatPrice(1187.5, 'en')).toBe('1,188');
  });

  it('handles null and undefined as zero', () => {
    expect(formatPrice(null, 'en')).toBe('0');
    expect(formatPrice(undefined, 'en')).toBe('0');
  });
});

describe('formatDate', () => {
  it('renders an em dash for missing or invalid dates', () => {
    expect(formatDate(null)).toBe('—');
    expect(formatDate('not-a-date')).toBe('—');
  });

  it('formats an ISO timestamp', () => {
    expect(formatDate('2027-01-15T00:00:00Z', 'en')).toContain('2027');
  });
});
