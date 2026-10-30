/** Tests for the TMA shell and the orders list. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../App';
import OrdersScreen from '../screens/OrdersScreen';
import api, { ApiError } from '../lib/api';
import { makeTranslator } from '../lib/i18n';

const t = makeTranslator('en');

function insideTelegram({ id = 555, language_code = 'en' } = {}) {
  window.Telegram = {
    WebApp: {
      initData: 'signed',
      initDataUnsafe: { user: { id, language_code } },
      colorScheme: 'dark',
      ready: vi.fn(),
      expand: vi.fn(),
      onEvent: vi.fn(),
      openLink: vi.fn(),
      HapticFeedback: {},
      BackButton: { show: vi.fn(), hide: vi.fn(), onClick: vi.fn(), offClick: vi.fn() },
    },
  };
}

beforeEach(() => {
  vi.spyOn(api, 'zones').mockResolvedValue(['com']);
  vi.spyOn(api, 'myDomains').mockResolvedValue([]);
  vi.spyOn(api, 'orders').mockResolvedValue([]);
  vi.spyOn(api, 'getUser').mockResolvedValue({ language: 'en' });
  vi.spyOn(api, 'ensureUser').mockResolvedValue({ language: 'en' });
});

afterEach(() => {
  delete window.Telegram;
});

// ----------------------------------------------------------------- app shell

describe('App', () => {
  it('supports running in regular web browser outside Telegram', async () => {
    // When opened in a regular web browser, the search tab renders cleanly
    render(<App />);

    expect(
      await screen.findByPlaceholderText(/Enter a domain name|Введите домен/),
    ).toBeInTheDocument();
  });

  it('renders the search tab by default inside Telegram', async () => {
    insideTelegram();

    render(<App />);

    expect(await screen.findByLabelText('Enter a domain name')).toBeInTheDocument();
  });

  it('switches between tabs', async () => {
    insideTelegram();
    const user = userEvent.setup();

    render(<App />);
    const domainBtns = await screen.findAllByRole('button', { name: /My domains/ });
    await user.click(domainBtns[0]);

    expect(await screen.findByText('You have no domains yet.')).toBeInTheDocument();

    const orderBtns = await screen.findAllByRole('button', { name: /Orders/ });
    await user.click(orderBtns[0]);
    expect(await screen.findByText('No orders yet.')).toBeInTheDocument();
  });

  it('adopts the language saved in the user profile', async () => {
    insideTelegram({ language_code: 'ru' });
    api.getUser.mockResolvedValue({ language: 'en' });
    api.ensureUser.mockResolvedValue({ language: 'en' });

    render(<App />);

    // The profile preference was set explicitly in the bot, so it beats the
    // Telegram client locale.
    expect((await screen.findAllByText('My domains')).length).toBeGreaterThan(0);
  });

  it('persists a language switch to the backend', async () => {
    insideTelegram();
    const setLang = vi.spyOn(api, 'setUserLanguage').mockResolvedValue({});
    const user = userEvent.setup();

    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Switch language' }));

    await waitFor(() => expect(setLang).toHaveBeenCalledWith(555, 'ru'));
    expect((await screen.findAllByText('Мои домены')).length).toBeGreaterThan(0);
  });

  it('still switches the UI when persisting the language fails', async () => {
    insideTelegram();
    vi.spyOn(api, 'setUserLanguage').mockRejectedValue(
      new ApiError('x', { status: 500 }),
    );
    const user = userEvent.setup();

    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'Switch language' }));

    expect((await screen.findAllByText('Мои домены')).length).toBeGreaterThan(0);
  });

  it('brands the 48px app bar with the D monogram', async () => {
    insideTelegram();

    render(<App />);

    // The monogram and wordmark are the only branding in the TMA, and the bar
    // is pinned to 48px so Telegram's own chrome does not get crowded out.
    const header = await waitFor(() => document.querySelector('header'));
    expect(header).toHaveClass('h-12');
    expect(header).toHaveTextContent('D');
    expect(header).toHaveTextContent('Dynadot');
  });

  it('switches language in a single tap, showing the target language', async () => {
    insideTelegram({ language_code: 'en' });
    vi.spyOn(api, 'setUserLanguage').mockResolvedValue({});
    const user = userEvent.setup();

    render(<App />);

    // The label advertises where the tap goes, not the current language.
    const toggle = await screen.findByRole('button', { name: 'Switch language' });
    expect(toggle).toHaveTextContent('RU');

    await user.click(toggle);

    expect((await screen.findAllByText('Мои домены')).length).toBeGreaterThan(0);
    expect(
      await screen.findByRole('button', { name: 'Switch language' }),
    ).toHaveTextContent('EN');
  });

  it('opens checkout when a search result is picked and hides the tab bar', async () => {
    insideTelegram();
    vi.spyOn(api, 'search').mockResolvedValue({
      results: [{ domain: 'pick.com', tld: 'com', available: true, price_rub: 1250 }],
    });
    vi.spyOn(api, 'contacts').mockResolvedValue([{ id: 'c1' }]);
    const user = userEvent.setup();

    render(<App />);
    await user.type(screen.getByLabelText('Enter a domain name'), 'pick{Enter}');
    await user.click(await screen.findByRole('button', { name: /pick.com/ }));

    expect(await screen.findByText('Using your saved details')).toBeInTheDocument();
    // Telegram's native back button replaces our own chrome in the overlay.
    expect(screen.queryByRole('button', { name: /My domains/ })).not.toBeInTheDocument();
  });

  it('works for a brand-new user with no profile yet', async () => {
    insideTelegram();
    api.getUser.mockRejectedValue(new ApiError('not found', { status: 404 }));

    render(<App />);

    expect(await screen.findByLabelText('Enter a domain name')).toBeInTheDocument();
  });
});

// -------------------------------------------------------------------- orders

describe('OrdersScreen', () => {
  it('shows the empty state', async () => {
    api.orders.mockResolvedValue([]);

    render(<OrdersScreen t={t} telegramId={1} />);

    expect(await screen.findByText('No orders yet.')).toBeInTheDocument();
  });

  it('renders an order with its status and price', async () => {
    api.orders.mockResolvedValue([
      {
        id: 'o1',
        domain_name: 'done.com',
        status: 'completed',
        price_rub: 1250,
        created_at: '2026-10-01T10:00:00Z',
      },
    ]);

    render(<OrdersScreen t={t} telegramId={1} />);

    expect(await screen.findByText('done.com')).toBeInTheDocument();
    expect(screen.getByText('Completed')).toBeInTheDocument();
    expect(screen.getByText(/1,250 ₽/)).toBeInTheDocument();
  });

  it('lets the customer resume an abandoned checkout', async () => {
    api.orders.mockResolvedValue([
      {
        id: 'o1',
        domain_name: 'unpaid.com',
        status: 'awaiting_payment',
        price_rub: 1250,
        created_at: '2026-10-01T10:00:00Z',
      },
    ]);
    const checkout = vi
      .spyOn(api, 'checkout')
      .mockResolvedValue({ payment_url: 'https://pay.test/2' });
    insideTelegram();
    const user = userEvent.setup();

    render(<OrdersScreen t={t} telegramId={1} />);
    await user.click(await screen.findByRole('button', { name: 'Pay' }));

    // Making the user search for the domain again is how that sale is lost.
    await waitFor(() => expect(checkout).toHaveBeenCalledWith('o1'));
  });

  it('offers no pay button on a completed order', async () => {
    api.orders.mockResolvedValue([
      {
        id: 'o1',
        domain_name: 'done.com',
        status: 'completed',
        price_rub: 1250,
        created_at: '2026-10-01T10:00:00Z',
      },
    ]);

    render(<OrdersScreen t={t} telegramId={1} />);
    await screen.findByText('done.com');

    expect(screen.queryByRole('button', { name: 'Pay' })).not.toBeInTheDocument();
  });

  it('explains a refund-pending order', async () => {
    api.orders.mockResolvedValue([
      {
        id: 'o1',
        domain_name: 'owed.com',
        status: 'refund_required',
        price_rub: 1250,
        created_at: '2026-10-01T10:00:00Z',
        error_message: 'domain taken',
      },
    ]);

    render(<OrdersScreen t={t} telegramId={1} />);

    expect(await screen.findByText(/could not get the domain/)).toBeInTheDocument();
  });

  it('offers a retry when the list fails to load', async () => {
    api.orders.mockRejectedValue(new ApiError('x', { status: 0 }));

    render(<OrdersScreen t={t} telegramId={1} />);

    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});
