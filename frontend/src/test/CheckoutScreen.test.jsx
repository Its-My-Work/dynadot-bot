/** Tests for the checkout flow.
 *
 * The money path: contact gate, order creation, payment link, and polling
 * until the background worker reaches a terminal state — including the
 * REFUND_REQUIRED outcome the user must be told about explicitly.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import CheckoutScreen, {
  isTerminal,
  statusMessage,
  TERMINAL_STATUSES,
} from '../screens/CheckoutScreen';
import { validateContact } from '../screens/ContactForm';
import api, { ApiError } from '../lib/api';
import { makeTranslator } from '../lib/i18n';

const t = makeTranslator('en');
const candidate = { domain: 'buyme.com', tld: 'com', available: true, price_rub: 1250 };

const CONTACT = {
  first_name: 'Ivan',
  last_name: 'Petrov',
  email: 'ivan@example.com',
  phone_country_code: '7',
  phone_number: '9990001122',
  address_line1: 'Lenina 1',
  city: 'Moscow',
  zip_code: '101000',
  country: 'RU',
};

let openLink;

beforeEach(() => {
  openLink = vi.fn();
  window.Telegram = { WebApp: { openLink, HapticFeedback: {} } };
});

afterEach(() => {
  delete window.Telegram;
});

// ------------------------------------------------------------------- helpers

describe('isTerminal', () => {
  it('treats every finished state as terminal so polling stops', () => {
    TERMINAL_STATUSES.forEach((status) => expect(isTerminal(status)).toBe(true));
  });

  it('keeps polling while the worker still has work to do', () => {
    ['draft', 'awaiting_payment', 'paid', 'registering'].forEach((status) =>
      expect(isTerminal(status)).toBe(false),
    );
  });
});

describe('statusMessage', () => {
  it('explains that a paid order is now being registered', () => {
    expect(statusMessage('paid', t).text).toContain('Registering');
  });

  it('tells the user about a refund rather than showing a bare failure', () => {
    // REFUND_REQUIRED means we have their money and could not deliver; saying
    // only "failed" here is how a support ticket is created.
    const message = statusMessage('refund_required', t);
    expect(message.text).toContain('refund');
    expect(message.tone).toBe('warn');
  });

  it('marks completion as a success tone', () => {
    expect(statusMessage('completed', t).tone).toBe('ok');
  });
});

describe('validateContact', () => {
  it('accepts a complete profile', () => {
    expect(validateContact(CONTACT).ok).toBe(true);
  });

  it('rejects a missing required field', () => {
    const result = validateContact({ ...CONTACT, city: '' });
    expect(result.ok).toBe(false);
    expect(result.missing).toContain('city');
  });

  it('rejects a malformed email', () => {
    expect(validateContact({ ...CONTACT, email: 'nope' }).ok).toBe(false);
  });

  it('requires a two-letter country code', () => {
    expect(validateContact({ ...CONTACT, country: 'RUS' }).ok).toBe(false);
  });
});

// ------------------------------------------------------------------- screens

describe('CheckoutScreen contact gate', () => {
  it('asks for owner details when the customer has none saved', async () => {
    vi.spyOn(api, 'contacts').mockResolvedValue([]);

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);

    expect(await screen.findByText('Owner details')).toBeInTheDocument();
    expect(screen.getByLabelText('First name')).toBeInTheDocument();
  });

  it('skips straight to payment when details are already saved', async () => {
    vi.spyOn(api, 'contacts').mockResolvedValue([{ id: 'c1', is_default: true }]);

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);

    // The product promise: a repeat buyer types nothing.
    expect(await screen.findByText('Using your saved details')).toBeInTheDocument();
    expect(screen.queryByLabelText('First name')).not.toBeInTheDocument();
  });

  it('assumes details are needed when the lookup fails', async () => {
    vi.spyOn(api, 'contacts').mockRejectedValue(new ApiError('x', { status: 500 }));

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);

    // Better to ask than to register with no owner data and fail later.
    expect(await screen.findByText('Owner details')).toBeInTheDocument();
  });

  it('saves a contact and moves on to payment', async () => {
    vi.spyOn(api, 'contacts').mockResolvedValue([]);
    const createSpy = vi.spyOn(api, 'createContact').mockResolvedValue({ id: 'c1' });
    const user = userEvent.setup();

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);
    await screen.findByText('Owner details');

    for (const [label, value] of [
      ['First name', CONTACT.first_name],
      ['Last name', CONTACT.last_name],
      ['Email', CONTACT.email],
      ['Phone', CONTACT.phone_number],
      ['Address', CONTACT.address_line1],
      ['City', CONTACT.city],
      ['ZIP', CONTACT.zip_code],
    ]) {
      await user.clear(screen.getByLabelText(label));
      await user.type(screen.getByLabelText(label), value);
    }
    await user.click(screen.getByRole('button', { name: 'Save and continue' }));

    await waitFor(() => expect(createSpy).toHaveBeenCalled());
    // Saved as the default so later purchases need no input at all.
    expect(createSpy.mock.calls[0][1].is_default).toBe(true);
    expect(await screen.findByText('Using your saved details')).toBeInTheDocument();
  });

  it('blocks submission of an incomplete contact form', async () => {
    vi.spyOn(api, 'contacts').mockResolvedValue([]);
    const createSpy = vi.spyOn(api, 'createContact');
    const user = userEvent.setup();

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);
    await screen.findByText('Owner details');
    await user.click(screen.getByRole('button', { name: 'Save and continue' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'fill in the required fields',
    );
    expect(createSpy).not.toHaveBeenCalled();
  });
});

describe('CheckoutScreen payment', () => {
  beforeEach(() => {
    vi.spyOn(api, 'contacts').mockResolvedValue([{ id: 'c1' }]);
  });

  it('shows the total and multiplies it by the chosen term', async () => {
    const user = userEvent.setup();
    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);

    await screen.findByText('Using your saved details');
    expect(screen.getByRole('button', { name: /Pay 1,250/ })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '2 yr' }));
    expect(screen.getByRole('button', { name: /Pay 2,500/ })).toBeInTheDocument();
  });

  it('creates the order, opens the gateway link and starts polling', async () => {
    const createOrder = vi
      .spyOn(api, 'createOrder')
      .mockResolvedValue({ id: 'o1', domain_name: 'buyme.com', price_rub: 1250, status: 'draft' });
    vi.spyOn(api, 'checkout').mockResolvedValue({
      payment_id: 'p1',
      order_id: 'o1',
      payment_url: 'https://pay.test/1',
      status: 'pending',
    });
    vi.spyOn(api, 'order').mockResolvedValue({
      id: 'o1',
      domain_name: 'buyme.com',
      price_rub: 1250,
      status: 'awaiting_payment',
    });
    const user = userEvent.setup();

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);
    await screen.findByText('Using your saved details');
    await user.click(screen.getByRole('button', { name: /Pay 1,250/ }));

    await waitFor(() => expect(createOrder).toHaveBeenCalled());
    expect(createOrder.mock.calls[0][1]).toEqual({
      domain_name: 'buyme.com',
      years: 1,
    });
    // Gateway URLs must go through openLink; openInvoice only takes Telegram
    // invoice links.
    await waitFor(() => expect(openLink).toHaveBeenCalledWith('https://pay.test/1'));
  });

  it('tells the user the domain was just taken on a 409', async () => {
    vi.spyOn(api, 'createOrder').mockRejectedValue(
      new ApiError('taken', { status: 409 }),
    );
    const user = userEvent.setup();

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);
    await screen.findByText('Using your saved details');
    await user.click(screen.getByRole('button', { name: /Pay 1,250/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('was just taken');
  });

  it('reports a registrar outage distinctly from a taken domain', async () => {
    vi.spyOn(api, 'createOrder').mockRejectedValue(
      new ApiError('down', { status: 503 }),
    );
    const user = userEvent.setup();

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);
    await screen.findByText('Using your saved details');
    await user.click(screen.getByRole('button', { name: /Pay 1,250/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'registrar is unavailable',
    );
  });

  it('offers an "I have paid" fallback when the webhook is lost', async () => {
    vi.spyOn(api, 'createOrder').mockResolvedValue({
      id: 'o1',
      domain_name: 'buyme.com',
      price_rub: 1250,
      status: 'draft',
    });
    vi.spyOn(api, 'checkout').mockResolvedValue({
      payment_id: 'p1',
      payment_url: 'https://pay.test/1',
    });
    const refresh = vi.spyOn(api, 'refreshPayment').mockResolvedValue({});
    vi.spyOn(api, 'order').mockResolvedValue({
      id: 'o1',
      domain_name: 'buyme.com',
      price_rub: 1250,
      status: 'paid',
    });
    const user = userEvent.setup();

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);
    await screen.findByText('Using your saved details');
    await user.click(screen.getByRole('button', { name: /Pay 1,250/ }));

    const paidButton = await screen.findByRole('button', { name: /I've paid/ });
    await user.click(paidButton);

    // Asks the backend to poll the gateway rather than trusting the client.
    await waitFor(() => expect(refresh).toHaveBeenCalledWith('p1'));
  });

  it('surfaces the refund outcome when the worker could not get the domain', async () => {
    vi.spyOn(api, 'createOrder').mockResolvedValue({
      id: 'o1',
      domain_name: 'buyme.com',
      price_rub: 1250,
      status: 'refund_required',
    });
    vi.spyOn(api, 'checkout').mockResolvedValue({ payment_id: 'p1' });
    const user = userEvent.setup();

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={vi.fn()} />);
    await screen.findByText('Using your saved details');
    await user.click(screen.getByRole('button', { name: /Pay 1,250/ }));

    expect(
      await screen.findByText(/could not get the domain/),
    ).toBeInTheDocument();
    // A terminal state gets a close button, not an endless spinner.
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument();
  });

  it('announces success when the domain is registered', async () => {
    vi.spyOn(api, 'createOrder').mockResolvedValue({
      id: 'o1',
      domain_name: 'buyme.com',
      price_rub: 1250,
      status: 'completed',
    });
    vi.spyOn(api, 'checkout').mockResolvedValue({ payment_id: 'p1' });
    const onDone = vi.fn();
    const user = userEvent.setup();

    render(<CheckoutScreen t={t} telegramId={1} candidate={candidate} onDone={onDone} />);
    await screen.findByText('Using your saved details');
    await user.click(screen.getByRole('button', { name: /Pay 1,250/ }));

    expect(await screen.findByText('Done! The domain is yours.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(onDone).toHaveBeenCalled();
  });
});
