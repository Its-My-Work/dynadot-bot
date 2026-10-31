/** Tests for the API client.
 *
 * The client is the only place that knows how backend failures map to UI
 * decisions, so the tests focus on status preservation (409 = taken, 503 =
 * registrar down) and on the language header the backend localises against.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import api, { ApiError, setLanguage } from '../lib/api';

function mockFetch(responder) {
  const spy = vi.fn(responder);
  global.fetch = spy;
  return spy;
}

function jsonResponse(body, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

beforeEach(() => {
  setLanguage('ru');
});

afterEach(() => {
  delete global.fetch;
});

describe('request building', () => {
  it('sends the language header so server errors come back localised', async () => {
    const spy = mockFetch(() => jsonResponse({ status: 'ok' }));
    setLanguage('en');

    await api.health();

    expect(spy.mock.calls[0][1].headers['X-Language']).toBe('en');
  });

  it('falls back to ru for an unknown language', async () => {
    const spy = mockFetch(() => jsonResponse({}));
    setLanguage('de');

    await api.health();

    expect(spy.mock.calls[0][1].headers['X-Language']).toBe('ru');
  });

  it('puts telegram_id in the query string, not the body', async () => {
    const spy = mockFetch(() => jsonResponse([]));

    await api.orders(12345);

    const [url] = spy.mock.calls[0];
    expect(url).toContain('telegram_id=12345');
  });

  it('omits empty query parameters instead of sending blanks', async () => {
    const spy = mockFetch(() => jsonResponse([]));

    await api.adminOrders({ status: '', limit: 10 });

    const [url] = spy.mock.calls[0];
    expect(url).not.toContain('status=');
    expect(url).toContain('limit=10');
  });

  it('serialises a JSON body with the right content type', async () => {
    const spy = mockFetch(() => jsonResponse({ id: 'o1' }));

    await api.createOrder(1, { domain_name: 'x.com', years: 2 });

    const [, options] = spy.mock.calls[0];
    expect(options.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(options.body)).toEqual({
      domain_name: 'x.com',
      years: 2,
    });
  });

  it('sends null tlds when no zone filter is selected', async () => {
    const spy = mockFetch(() => jsonResponse({ results: [] }));

    await api.search('mysite', []);

    expect(JSON.parse(spy.mock.calls[0][1].body)).toMatchObject({
      query: 'mysite',
      tlds: null,
    });
  });
});

describe('error mapping', () => {
  it('marks a 409 as a conflict so the UI can say "just taken"', async () => {
    mockFetch(() => jsonResponse({ detail: 'Занят' }, 409));

    const error = await api
      .createOrder(1, { domain_name: 'taken.com' })
      .catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.isConflict).toBe(true);
    expect(error.message).toBe('Занят');
  });

  it('marks a 503 as unavailable so a retry is offered', async () => {
    mockFetch(() => jsonResponse({ detail: 'Регистратор недоступен' }, 503));

    const error = await api.search('x').catch((e) => e);

    expect(error.isUnavailable).toBe(true);
  });

  it('reports a network failure as status 0', async () => {
    mockFetch(() => Promise.reject(new TypeError('failed to fetch')));

    const error = await api.health().catch((e) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.message).toBe('network_error');
  });

  it('rethrows an abort so a superseded search is not shown as an error', async () => {
    mockFetch(() => {
      const abort = new Error('aborted');
      abort.name = 'AbortError';
      return Promise.reject(abort);
    });

    const error = await api.search('x').catch((e) => e);

    expect(error.name).toBe('AbortError');
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it('extracts the first message from a FastAPI validation error', async () => {
    mockFetch(() =>
      jsonResponse({ detail: [{ msg: 'string too short' }] }, 422),
    );

    const error = await api.createOrder(1, { domain_name: 'a' }).catch((e) => e);

    expect(error.message).toBe('string too short');
  });

  it('survives a non-JSON error body', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 502,
        text: () => Promise.resolve('<html>bad gateway</html>'),
      }),
    );

    const error = await api.health().catch((e) => e);

    expect(error.status).toBe(502);
  });

  it('returns null for a 204 instead of trying to parse it', async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 204, text: () => Promise.resolve('') }),
    );

    await expect(api.health()).resolves.toBeNull();
  });
});
