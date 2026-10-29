/** "My domains" + nameserver management.
 *
 * NS editing is the one genuinely technical thing in the TMA, so it is a
 * textarea of one-server-per-line rather than a dynamic row editor: users
 * paste the two lines Cloudflare gives them, and anything fancier just adds
 * taps. The registrar call is slow, so the control is disabled while in
 * flight to prevent a double submit.
 */
import { useCallback, useEffect, useState } from 'react';

import api from '../lib/api';
import { formatDate } from '../lib/i18n';
import { haptic } from '../lib/telegram';
import StatusBadge from '../components/StatusBadge';
import { EmptyState, ErrorNote, SkeletonList, Spinner, Toast } from '../components/ui';

export function parseNameservers(text) {
  return text
    .split(/[\n,]/)
    .map((line) => line.trim().toLowerCase())
    .filter(Boolean);
}

export default function DomainsScreen({ t, telegramId }) {
  const [domains, setDomains] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(null);
  const [toast, setToast] = useState(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const list = await api.myDomains(telegramId);
      setDomains(Array.isArray(list) ? list : []);
    } catch {
      setError(t('common.network'));
      setDomains(null);
    }
  }, [telegramId, t]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return <ErrorNote message={error} onRetry={load} retryLabel={t('common.retry')} />;
  }

  if (domains === null) return <SkeletonList rows={3} />;

  if (domains.length === 0) {
    return <EmptyState icon="◇" title={t('domains.empty')} />;
  }

  return (
    <>
      <ul className="space-y-2">
        {domains.map((domain) => (
          <li key={domain.id} className="card p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate font-medium">{domain.name}</p>
                <p className="text-xs text-muted">
                  {t('domains.expires', {
                    date: formatDate(domain.expires_at, t.language),
                  })}
                </p>
              </div>
              <StatusBadge
                status={domain.status}
                label={t(`status.${domain.status}`) === `status.${domain.status}`
                  ? domain.status
                  : t(`status.${domain.status}`)}
              />
            </div>

            <div className="mt-3 border-t border-line pt-3">
              <p className="text-xs font-medium text-muted">{t('domains.ns')}</p>
              <p className="mt-1 break-all font-mono text-xs text-ink">
                {domain.nameservers || '—'}
              </p>

              {editing === domain.id ? (
                <NameserverEditor
                  t={t}
                  domain={domain}
                  onCancel={() => setEditing(null)}
                  onSaved={(updated) => {
                    setDomains((current) =>
                      current.map((d) => (d.id === updated.id ? updated : d)),
                    );
                    setEditing(null);
                    setToast({ message: t('domains.ns_saved'), tone: 'ok' });
                    haptic('success');
                  }}
                />
              ) : (
                <button
                  type="button"
                  className="btn-ghost mt-3 w-full py-2 text-sm"
                  onClick={() => setEditing(domain.id)}
                >
                  {t('domains.ns_edit')}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </>
  );
}

function NameserverEditor({ t, domain, onCancel, onSaved }) {
  const [text, setText] = useState((domain.nameservers || '').split(',').join('\n'));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function save() {
    const list = parseNameservers(text);
    if (list.length === 0) {
      setError(t('domains.ns_empty'));
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const updated = await api.setNameservers(domain.id, list);
      onSaved(updated);
    } catch {
      haptic('error');
      setError(t('domains.ns_failed'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-3 space-y-2">
      <textarea
        className="input min-h-[5.5rem] font-mono text-sm"
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={'ada.ns.cloudflare.com\nbob.ns.cloudflare.com'}
        aria-label={t('domains.ns')}
        spellCheck="false"
        autoCapitalize="none"
      />
      <p className="text-xs text-faint">{t('domains.ns_hint')}</p>
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel} disabled={saving}>
          {t('common.cancel')}
        </button>
        <button type="button" className="btn-primary" onClick={save} disabled={saving}>
          {saving ? <Spinner /> : null}
          {t('domains.ns_save')}
        </button>
      </div>
    </div>
  );
}
