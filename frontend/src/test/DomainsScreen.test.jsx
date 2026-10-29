/** Tests for "My domains" and nameserver delegation. */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import DomainsScreen, { parseNameservers } from '../screens/DomainsScreen';
import api, { ApiError } from '../lib/api';
import { makeTranslator } from '../lib/i18n';

const t = makeTranslator('en');

const DOMAIN = {
  id: 'd1',
  name: 'mysite.com',
  tld: 'com',
  status: 'active',
  expires_at: '2027-01-15T00:00:00Z',
  nameservers: 'ns1.dynadot.com,ns2.dynadot.com',
  auto_renew: false,
};

describe('parseNameservers', () => {
  it('accepts newline-separated input', () => {
    expect(parseNameservers('a.ns.com\nb.ns.com')).toEqual([
      'a.ns.com',
      'b.ns.com',
    ]);
  });

  it('accepts comma-separated input too', () => {
    // Users paste both shapes out of Cloudflare's UI.
    expect(parseNameservers('a.ns.com, b.ns.com')).toEqual([
      'a.ns.com',
      'b.ns.com',
    ]);
  });

  it('lowercases and trims', () => {
    expect(parseNameservers('  A.NS.COM  ')).toEqual(['a.ns.com']);
  });

  it('drops blank lines rather than sending empty strings', () => {
    expect(parseNameservers('a.ns.com\n\n\n')).toEqual(['a.ns.com']);
  });

  it('returns an empty array for whitespace only', () => {
    expect(parseNameservers('   \n  ')).toEqual([]);
  });
});

describe('DomainsScreen', () => {
  it('shows a skeleton before the list arrives', () => {
    vi.spyOn(api, 'myDomains').mockReturnValue(new Promise(() => {}));

    render(<DomainsScreen t={t} telegramId={1} />);

    expect(screen.getByTestId('skeleton')).toBeInTheDocument();
  });

  it('renders the empty state for a new customer', async () => {
    vi.spyOn(api, 'myDomains').mockResolvedValue([]);

    render(<DomainsScreen t={t} telegramId={1} />);

    expect(await screen.findByText('You have no domains yet.')).toBeInTheDocument();
  });

  it('lists domains with their expiry and nameservers', async () => {
    vi.spyOn(api, 'myDomains').mockResolvedValue([DOMAIN]);

    render(<DomainsScreen t={t} telegramId={1} />);

    expect(await screen.findByText('mysite.com')).toBeInTheDocument();
    expect(screen.getByText(/Expires/)).toBeInTheDocument();
    expect(screen.getByText('ns1.dynadot.com,ns2.dynadot.com')).toBeInTheDocument();
  });

  it('offers a retry when the list cannot be loaded', async () => {
    const spy = vi
      .spyOn(api, 'myDomains')
      .mockRejectedValueOnce(new ApiError('x', { status: 0 }))
      .mockResolvedValueOnce([DOMAIN]);
    const user = userEvent.setup();

    render(<DomainsScreen t={t} telegramId={1} />);

    await user.click(await screen.findByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('mysite.com')).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it('saves edited nameservers and confirms the change', async () => {
    vi.spyOn(api, 'myDomains').mockResolvedValue([DOMAIN]);
    const setSpy = vi.spyOn(api, 'setNameservers').mockResolvedValue({
      ...DOMAIN,
      nameservers: 'ada.ns.cloudflare.com,bob.ns.cloudflare.com',
    });
    const user = userEvent.setup();

    render(<DomainsScreen t={t} telegramId={1} />);
    await user.click(await screen.findByRole('button', { name: 'Edit NS' }));

    const textarea = screen.getByLabelText('Nameservers');
    await user.clear(textarea);
    await user.type(textarea, 'ada.ns.cloudflare.com\nbob.ns.cloudflare.com');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(setSpy).toHaveBeenCalledWith('d1', [
        'ada.ns.cloudflare.com',
        'bob.ns.cloudflare.com',
      ]),
    );
    expect(await screen.findByRole('status')).toHaveTextContent(
      'Nameservers updated',
    );
  });

  it('refuses to submit an empty nameserver list', async () => {
    vi.spyOn(api, 'myDomains').mockResolvedValue([DOMAIN]);
    const setSpy = vi.spyOn(api, 'setNameservers');
    const user = userEvent.setup();

    render(<DomainsScreen t={t} telegramId={1} />);
    await user.click(await screen.findByRole('button', { name: 'Edit NS' }));
    await user.clear(screen.getByLabelText('Nameservers'));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'at least one server',
    );
    // Never send an empty delegation: it would take the site offline.
    expect(setSpy).not.toHaveBeenCalled();
  });

  it('keeps the editor open and reports failure when the registrar rejects', async () => {
    vi.spyOn(api, 'myDomains').mockResolvedValue([DOMAIN]);
    vi.spyOn(api, 'setNameservers').mockRejectedValue(
      new ApiError('down', { status: 503 }),
    );
    const user = userEvent.setup();

    render(<DomainsScreen t={t} telegramId={1} />);
    await user.click(await screen.findByRole('button', { name: 'Edit NS' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not update nameservers',
    );
    // The user's typed value is preserved so they can retry.
    expect(screen.getByLabelText('Nameservers')).toBeInTheDocument();
  });

  it('cancelling the editor discards the draft', async () => {
    vi.spyOn(api, 'myDomains').mockResolvedValue([DOMAIN]);
    const user = userEvent.setup();

    render(<DomainsScreen t={t} telegramId={1} />);
    await user.click(await screen.findByRole('button', { name: 'Edit NS' }));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByLabelText('Nameservers')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit NS' })).toBeInTheDocument();
  });
});
