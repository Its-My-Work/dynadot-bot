/** Component tests for the search screen.
 *
 * Covers the behaviours that protect the registrar queue and the user's
 * patience: debounced input, abort of superseded requests, taken results kept
 * visible but unbuyable, and recoverable errors.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import SearchScreen, { sanitizeQuery } from '../screens/SearchScreen';
import api, { ApiError } from '../lib/api';
import { makeTranslator } from '../lib/i18n';

const t = makeTranslator('en');

beforeEach(() => {
  vi.spyOn(api, 'zones').mockResolvedValue(['com', 'io']);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('sanitizeQuery', () => {
  it.each([
        ['  MySite.COM ', 'mysite.com'],
    ['..leading', 'leading'],
    ['bad name!@#', 'badname'],
    ['keep-hyphens-123', 'keep-hyphens-123'],
  ])('normalises %s', (input, expected) => {
    expect(sanitizeQuery(input)).toBe(expected);
  });
});

describe('SearchScreen', () => {
  it('shows an inviting empty state before any input', async () => {
    render(<SearchScreen t={t} onSelect={vi.fn()} />);

    expect(await screen.findByText('Start with a domain name')).toBeInTheDocument();
  });

  it('loads category filter chips and catalog button', async () => {
    render(<SearchScreen t={t} onSelect={vi.fn()} />);

    expect(await screen.findByText('All zones')).toBeInTheDocument();
    expect(screen.getByText('🔥 Top')).toBeInTheDocument();
    expect(screen.getByText('🤖 AI & IT')).toBeInTheDocument();
    expect(screen.getByText('📚 All 800+ Zones Catalog')).toBeInTheDocument();
  });

  it('still renders when the zone list fails to load', async () => {
    api.zones.mockRejectedValue(new ApiError('boom', { status: 500 }));
    render(<SearchScreen t={t} onSelect={vi.fn()} />);

    expect(await screen.findByText('Start with a domain name')).toBeInTheDocument();
  });

  it('debounces typing into a single search call', async () => {
    const searchSpy = vi.spyOn(api, 'search').mockResolvedValue({ results: [] });
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={vi.fn()} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'mysite');

    // One request for six keystrokes: each search is a serialised registrar
    // call, so per-keystroke firing would queue garbage behind the real query.
    await waitFor(() => expect(searchSpy).toHaveBeenCalledTimes(1), {
      timeout: 2000,
    });
    expect(searchSpy.mock.calls[0][0]).toBe('mysite');
  });

  it('searches immediately on Enter without waiting for the debounce', async () => {
    const searchSpy = vi.spyOn(api, 'search').mockResolvedValue({ results: [] });
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={vi.fn()} />);
    const input = screen.getByLabelText('Enter a domain name');
    await user.type(input, 'instant{Enter}');

    await waitFor(() => expect(searchSpy).toHaveBeenCalled());
  });

  it('renders available results as buy buttons with the RUB price', async () => {
    vi.spyOn(api, 'search').mockResolvedValue({
      results: [
        {
          domain: 'mysite.com',
          tld: 'com',
          available: true,
          price_rub: 1250,
          is_premium: false,
        },
      ],
    });
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={vi.fn()} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'mysite{Enter}');

    expect(await screen.findByText('mysite.com')).toBeInTheDocument();
    expect(await screen.findByText('1,250 ₽')).toBeInTheDocument();
  });

  it('keeps taken results visible but not buyable', async () => {
    vi.spyOn(api, 'search').mockResolvedValue({
      results: [
        { domain: 'taken.com', tld: 'com', available: false },
        { domain: 'free.io', tld: 'io', available: true, price_rub: 3000 },
      ],
    });
    const onSelect = vi.fn();
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={onSelect} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'x{Enter}');

    // Hiding taken rows makes the zone grid look random; they stay, dimmed.
    expect(await screen.findByText('taken.com')).toBeInTheDocument();
    expect(screen.getByText('Taken')).toBeInTheDocument();
    // Only the available row has a price button.
    expect(screen.getAllByRole('button', { name: /RUB/ })).toHaveLength(1);
  });

  it('calls onSelect with the chosen result', async () => {
    const result = {
      domain: 'pick.com',
      tld: 'com',
      available: true,
      price_rub: 1500,
    };
    vi.spyOn(api, 'search').mockResolvedValue({ results: [result] });
    const onSelect = vi.fn();
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={onSelect} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'pick{Enter}');
    await user.click(await screen.findByRole('button', { name: /pick.com/ }));

    expect(onSelect).toHaveBeenCalledWith(result);
  });

  it('shows a retryable error when the registrar is down', async () => {
    vi.spyOn(api, 'search').mockRejectedValue(
      new ApiError('unavailable', { status: 503 }),
    );
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={vi.fn()} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'down{Enter}');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The registrar is unavailable');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('reports an invalid name distinctly from a server outage', async () => {
    vi.spyOn(api, 'search').mockRejectedValue(
      new ApiError('bad', { status: 422 }),
    );
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={vi.fn()} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'x{Enter}');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Invalid domain name',
    );
  });

  it('reports a lost connection', async () => {
    vi.spyOn(api, 'search').mockRejectedValue(
      new ApiError('network_error', { status: 0 }),
    );
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={vi.fn()} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'x{Enter}');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No connection to the server',
    );
  });

  it('ignores an aborted request instead of flashing an error', async () => {
    const abort = new Error('aborted');
    abort.name = 'AbortError';
    vi.spyOn(api, 'search').mockRejectedValue(abort);
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={vi.fn()} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'x{Enter}');

    await waitFor(() => expect(api.search).toHaveBeenCalled());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('passes the selected category filter to the backend', async () => {
    const searchSpy = vi.spyOn(api, 'search').mockResolvedValue({ results: [] });
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={vi.fn()} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'zoned');
    await user.click(await screen.findByText('🤖 AI & IT'));

    await waitFor(() => {
      const last = searchSpy.mock.calls.at(-1);
      expect(last[3]).toMatchObject({ category: 'tech' });
    });
  });

  it('shows the empty state when nothing matches', async () => {
    vi.spyOn(api, 'search').mockResolvedValue({ results: [] });
    const user = userEvent.setup();

    render(<SearchScreen t={t} onSelect={vi.fn()} />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'none{Enter}');

    expect(await screen.findByText('Nothing found.')).toBeInTheDocument();
  });
});
