/** Tests for the admin panel.
 *
 * Focus on the operator-critical paths: the refund queue being front and
 * centre, dead-lettered tasks being one click from a retry, and revenue not
 * counting money we owe back.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AdminApp from '../AdminApp';
import api, { ApiError } from '../lib/api';

const STATS = {
  users: 12,
  orders_total: 30,
  orders_completed: 21,
  orders_awaiting_payment: 3,
  refunds_pending: 2,
  domains_active: 19,
  revenue_rub: 42000,
  tasks_queued: 1,
  tasks_dead_letter: 4,
};

beforeEach(() => {
  vi.spyOn(api, 'adminStats').mockResolvedValue(STATS);
  vi.spyOn(api, 'adminRefunds').mockResolvedValue([]);
  vi.spyOn(api, 'adminOrders').mockResolvedValue([]);
  vi.spyOn(api, 'adminTasks').mockResolvedValue([]);
  vi.spyOn(api, 'adminPricing').mockResolvedValue([]);
  vi.spyOn(api, 'adminApiLogs').mockResolvedValue([]);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('overview', () => {
  it('shares the TMA branding in a 48px bar', async () => {
    render(<AdminApp />);

    // One visual system across both surfaces: same bar height, same monogram.
    const header = await waitFor(() => document.querySelector('header'));
    expect(header).toHaveClass('h-12');
    expect(header).toHaveTextContent('Dynadot');
    expect(header).toHaveTextContent('Admin');
  });

  it('shows the headline numbers', async () => {
    render(<AdminApp />);

    expect(await screen.findByText('42 000 ₽')).toBeInTheDocument();
    expect(screen.getByText('21')).toBeInTheDocument();
    expect(screen.getByText('19')).toBeInTheDocument();
  });

  it('badges the refund count in the nav so it cannot be missed', async () => {
    render(<AdminApp />);

    // Money owed to customers is the only urgent thing in this system.
    const refundsTab = await screen.findByRole('button', { name: /Refunds/ });
    expect(refundsTab).toHaveTextContent('2');
  });

  it('renders a skeleton until stats arrive', () => {
    api.adminStats.mockReturnValue(new Promise(() => {}));

    render(<AdminApp />);

    expect(screen.getByTestId('skeleton')).toBeInTheDocument();
  });

  it('survives a stats failure without blanking the panel', async () => {
    api.adminStats.mockRejectedValue(new ApiError('x', { status: 500 }));
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(screen.getByRole('button', { name: /Queue/ }));

    // Other views must still be reachable when the dashboard query fails.
    expect(await screen.findByText('Queue is empty')).toBeInTheDocument();
  });
});

describe('refunds', () => {
  it('lists pending refunds with their reason', async () => {
    api.adminRefunds.mockResolvedValue([
      {
        id: 'o1',
        domain_name: 'owed.com',
        price_rub: 1500,
        error_message: 'domain taken',
        created_at: '2026-10-01T10:00:00Z',
      },
    ]);
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Refunds/ }));

    expect(await screen.findByText('owed.com')).toBeInTheDocument();
    expect(screen.getByText('domain taken')).toBeInTheDocument();
    expect(screen.getByText('1 500 ₽')).toBeInTheDocument();
  });

  it('resolves a refund after confirmation and refreshes the queue', async () => {
    api.adminRefunds
      .mockResolvedValueOnce([
        {
          id: 'o1',
          domain_name: 'owed.com',
          price_rub: 1500,
          created_at: '2026-10-01T10:00:00Z',
        },
      ])
      .mockResolvedValueOnce([]);
    const resolve = vi.spyOn(api, 'resolveRefund').mockResolvedValue({});
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Refunds/ }));
    await user.click(await screen.findByRole('button', { name: /Mark refunded/ }));

    await waitFor(() => expect(resolve).toHaveBeenCalledWith('o1', expect.any(String)));
    expect(await screen.findByText('No pending refunds')).toBeInTheDocument();
  });

  it('does nothing when the operator cancels the confirmation', async () => {
    api.adminRefunds.mockResolvedValue([
      { id: 'o1', domain_name: 'owed.com', price_rub: 1500, created_at: null },
    ]);
    const resolve = vi.spyOn(api, 'resolveRefund');
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Refunds/ }));
    await user.click(await screen.findByRole('button', { name: /Mark refunded/ }));

    expect(resolve).not.toHaveBeenCalled();
  });

  it('shows a clean empty state when nothing is owed', async () => {
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Refunds/ }));

    expect(await screen.findByText('No pending refunds')).toBeInTheDocument();
  });
});

describe('task queue', () => {
  const DEAD = {
    id: 't1',
    operation: 'register_domain',
    status: 'dead_letter',
    priority: 0,
    attempts: 4,
    max_attempts: 4,
    last_error: 'registrar timeout',
    scheduled_at: '2026-10-01T10:00:00Z',
    created_at: '2026-10-01T09:00:00Z',
  };

  it('shows attempts and the last error for a dead-lettered task', async () => {
    api.adminTasks.mockResolvedValue([DEAD]);
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Queue/ }));

    expect(await screen.findByText('register_domain')).toBeInTheDocument();
    expect(screen.getByText('4/4')).toBeInTheDocument();
    expect(screen.getByText('registrar timeout')).toBeInTheDocument();
  });

  it('requeues a dead-lettered task in one click', async () => {
    api.adminTasks.mockResolvedValue([DEAD]);
    const retry = vi.spyOn(api, 'retryTask').mockResolvedValue({});
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Queue/ }));
    await user.click(await screen.findByRole('button', { name: 'Retry' }));

    // A registrar outage must be recoverable without a database console.
    await waitFor(() => expect(retry).toHaveBeenCalledWith('t1'));
  });

  it('offers no retry for a task that is still queued', async () => {
    api.adminTasks.mockResolvedValue([{ ...DEAD, status: 'queued', attempts: 0 }]);
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Queue/ }));
    await screen.findByText('register_domain');

    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });
});

describe('orders view', () => {
  it('filters by status', async () => {
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /^Orders$/ }));
    await user.click(await screen.findByRole('button', { name: 'completed' }));

    await waitFor(() =>
      expect(api.adminOrders).toHaveBeenCalledWith({ status: 'completed' }),
    );
  });

  it('shows the money breakdown operators need', async () => {
    api.adminOrders.mockResolvedValue([
      {
        id: 'o1',
        domain_name: 'sold.com',
        status: 'completed',
        price_rub: 1250,
        cost_usd: 10,
        markup_percent: 25,
        created_at: '2026-10-01T10:00:00Z',
      },
    ]);
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /^Orders$/ }));

    expect(await screen.findByText('sold.com')).toBeInTheDocument();
    expect(screen.getByText('$10.00')).toBeInTheDocument();
    expect(screen.getByText('25%')).toBeInTheDocument();
  });
});

describe('pricing view', () => {
  const ZONE = {
    id: 'p1',
    tld: 'com',
    register_cost_usd: 10,
    markup_percent: 25,
    fixed_price_rub: null,
    is_enabled: true,
    is_featured: false,
  };

  it('warns that changes only affect new orders', async () => {
    api.adminPricing.mockResolvedValue([ZONE]);
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Pricing/ }));

    // Existing orders froze their price at checkout — saying so prevents a
    // support escalation about "the price changed".
    expect(await screen.findByText(/price frozen/)).toBeInTheDocument();
  });

  it('saves an edited markup on blur', async () => {
    api.adminPricing.mockResolvedValue([ZONE]);
    const update = vi.spyOn(api, 'updatePricing').mockResolvedValue({ ...ZONE, markup_percent: 40 });
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Pricing/ }));

    const input = (await screen.findAllByDisplayValue('25'))[0];
    await user.clear(input);
    await user.type(input, '40');
    await user.tab();

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith('com', { markup_percent: 40 }),
    );
  });

  it('ignores a non-numeric markup instead of sending NaN', async () => {
    api.adminPricing.mockResolvedValue([ZONE]);
    const update = vi.spyOn(api, 'updatePricing');
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Pricing/ }));

    const input = (await screen.findAllByDisplayValue('25'))[0];
    await user.clear(input);
    await user.type(input, 'abc');
    await user.tab();

    expect(update).not.toHaveBeenCalled();
  });

  it('toggles a zone on and off', async () => {
    api.adminPricing.mockResolvedValue([ZONE]);
    const update = vi
      .spyOn(api, 'updatePricing')
      .mockResolvedValue({ ...ZONE, is_enabled: false });
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Pricing/ }));
    await user.click(await screen.findByRole('button', { name: 'on' }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith('com', { is_enabled: false }),
    );
  });

  it('explains an empty pricing table', async () => {
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /Pricing/ }));

    expect(await screen.findByText('No zones configured')).toBeInTheDocument();
  });
});

describe('api log view', () => {
  it('renders call rows with timing', async () => {
    api.adminApiLogs.mockResolvedValue([
      {
        id: 'l1',
        endpoint: '/domains/x.com/register',
        method: 'POST',
        status: 'success',
        http_status: 200,
        duration_ms: 812.4,
        attempt: 1,
        created_at: '2026-10-01T10:00:00Z',
      },
    ]);
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /API log/ }));

    expect(await screen.findByText('/domains/x.com/register')).toBeInTheDocument();
    expect(screen.getByText('812')).toBeInTheDocument();
  });

  it('shows an empty state before any call is made', async () => {
    const user = userEvent.setup();

    render(<AdminApp />);
    await user.click(await screen.findByRole('button', { name: /API log/ }));

    expect(await screen.findByText('No API calls yet')).toBeInTheDocument();
  });
});
