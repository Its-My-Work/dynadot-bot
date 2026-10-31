/** User profile and WHOIS registrant contact management. */
import { useCallback, useEffect, useState } from 'react';

import api from '../lib/api';
import { haptic } from '../lib/telegram';
import ContactForm from './ContactForm';
import { ErrorNote, SkeletonList, Spinner, Toast } from '../components/ui';

export default function ProfileScreen({ t, telegramId }) {
  const [contacts, setContacts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState(null);

  const loadProfile = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await api.contacts(telegramId);
      setContacts(Array.isArray(list) ? list : []);
    } catch {
      setError(t('common.network'));
    } finally {
      setLoading(false);
    }
  }, [telegramId, t]);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const defaultContact = contacts?.find((c) => c.is_default) || contacts?.[0] || null;

  const handleSaveContact = async (values) => {
    setSaving(true);
    try {
      if (defaultContact?.id) {
        await api.updateContact(defaultContact.id, values);
      } else {
        await api.createContact(telegramId, { ...values, is_default: true });
      }
      await loadProfile();
      setEditing(false);
      setToast({ message: t('profile.saved'), tone: 'ok' });
      haptic('success');
    } catch {
      setToast({ message: t('common.error'), tone: 'danger' });
      haptic('error');
    } finally {
      setSaving(false);
    }
  };

  if (loading && !contacts) {
    return <SkeletonList rows={3} />;
  }

  if (error && !contacts) {
    return <ErrorNote message={error} onRetry={loadProfile} retryLabel={t('common.retry')} />;
  }

  return (
    <div className="space-y-4">
      {/* User Header Card */}
      <div className="card p-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="h-11 w-11 rounded-full bg-accent/15 border border-accent/30 flex items-center justify-center text-accent text-lg font-bold">
            👤
          </div>
          <div>
            <p className="font-bold text-sm text-ink flex items-center gap-2">
              <span>{t('profile.title')}</span>
              {defaultContact ? (
                <span className="badge text-[10px] bg-accent/20 text-accent font-semibold px-2 py-0.5">
                  ✓ {t('profile.verified')}
                </span>
              ) : null}
            </p>
            <p className="text-xs text-muted font-mono mt-0.5">
              ID: {telegramId || '—'}
            </p>
          </div>
        </div>
      </div>

      {/* Information Banner */}
      <div className="rounded-xl border border-accent/25 bg-accent/5 p-3 space-y-1">
        <p className="text-xs font-semibold text-accent flex items-center gap-1.5">
          <span>ℹ️</span>
          <span>{t('profile.whois_title')}</span>
        </p>
        <p className="text-[11px] text-muted leading-relaxed">
          {t('profile.whois_desc')}
        </p>
      </div>

      {/* Profile Form (Edit Mode) */}
      {editing ? (
        <div className="card p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-card-border/60 pb-2">
            <h3 className="text-sm font-bold text-ink">
              {defaultContact ? t('profile.edit_title') : t('profile.create_title')}
            </h3>
            <button
              type="button"
              className="text-xs text-muted hover:text-ink cursor-pointer"
              onClick={() => setEditing(false)}
            >
              ✕ {t('common.cancel')}
            </button>
          </div>

          <ContactForm
            t={t}
            initialValues={defaultContact}
            onSubmit={handleSaveContact}
            onCancel={() => setEditing(false)}
            submitting={saving}
          />
        </div>
      ) : (
        /* Profile Details (View Mode) */
        <div className="card p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-card-border/60 pb-2">
            <h3 className="text-sm font-bold text-ink flex items-center gap-2">
              <span>📋</span>
              <span>{t('profile.owner_data')}</span>
            </h3>
            <button
              type="button"
              onClick={() => {
                haptic('light');
                setEditing(true);
              }}
              className="badge border border-accent/40 bg-card text-accent font-semibold px-2.5 py-1 text-xs cursor-pointer hover:bg-accent/10"
            >
              {defaultContact ? t('profile.edit_btn') : t('profile.create_btn')}
            </button>
          </div>

          {defaultContact ? (
            <div className="space-y-2.5 text-xs">
              <div className="flex justify-between items-center py-1 border-b border-card-border/30">
                <span className="text-muted">{t('contact.first_name')} / {t('contact.last_name')}:</span>
                <span className="font-semibold text-ink">
                  {defaultContact.first_name} {defaultContact.last_name}
                </span>
              </div>

              <div className="flex justify-between items-center py-1 border-b border-card-border/30">
                <span className="text-muted">{t('contact.email')}:</span>
                <span className="font-mono text-ink">{defaultContact.email}</span>
              </div>

              <div className="flex justify-between items-center py-1 border-b border-card-border/30">
                <span className="text-muted">{t('contact.phone')}:</span>
                <span className="font-mono text-ink">
                  +{defaultContact.phone_country_code} {defaultContact.phone_number}
                </span>
              </div>

              {defaultContact.organization ? (
                <div className="flex justify-between items-center py-1 border-b border-card-border/30">
                  <span className="text-muted">{t('contact.organization')}:</span>
                  <span className="font-medium text-ink">{defaultContact.organization}</span>
                </div>
              ) : null}

              <div className="flex justify-between items-center py-1 border-b border-card-border/30">
                <span className="text-muted">{t('contact.country')} / {t('contact.city')}:</span>
                <span className="text-ink">
                  {defaultContact.country}, {defaultContact.city}
                </span>
              </div>

              <div className="flex justify-between items-center py-1">
                <span className="text-muted">{t('contact.address')}:</span>
                <span className="text-ink text-right max-w-[200px] truncate">
                  {defaultContact.address_line1} {defaultContact.zip_code ? `(${defaultContact.zip_code})` : ''}
                </span>
              </div>
            </div>
          ) : (
            <div className="py-6 text-center space-y-3">
              <p className="text-xs text-muted">
                {t('profile.empty_prompt')}
              </p>
              <button
                type="button"
                onClick={() => {
                  haptic('light');
                  setEditing(true);
                }}
                className="btn-accent px-4 py-2 text-xs font-bold cursor-pointer"
              >
                + {t('profile.create_btn')}
              </button>
            </div>
          )}
        </div>
      )}

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </div>
  );
}
