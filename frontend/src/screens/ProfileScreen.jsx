/** User profile, account authorization/linking, and WHOIS registrant contact management. */
import { useCallback, useEffect, useState } from 'react';

import api from '../lib/api';
import { clearSessionUser, haptic } from '../lib/telegram';
import ContactForm from './ContactForm';
import AuthModal from '../components/AuthModal';
import { ErrorNote, SkeletonList, Toast } from '../components/ui';

export default function ProfileScreen({ t, telegramId }) {
  const [user, setUser] = useState(null);
  const [contacts, setContacts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState(null);

  // Account linking state
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [isLinkingEmail, setIsLinkingEmail] = useState(false);
  const [linkEmailVal, setLinkEmailVal] = useState('');
  const [linkPassVal, setLinkPassVal] = useState('');
  const [linkSaving, setLinkSaving] = useState(false);
  const [generatingTgLink, setGeneratingTgLink] = useState(false);

  const loadProfile = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [u, list] = await Promise.all([
        api.getMe(telegramId).catch(() => api.ensureUser(telegramId).catch(() => null)),
        api.contacts(telegramId).catch(() => []),
      ]);
      if (u) setUser(u);
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

  const handleLinkEmail = async (e) => {
    e.preventDefault();
    if (!linkEmailVal || !linkPassVal) return;
    setLinkSaving(true);
    try {
      const res = await api.linkEmail(telegramId, linkEmailVal, linkPassVal);
      setUser(res.user);
      setIsLinkingEmail(false);
      setLinkEmailVal('');
      setLinkPassVal('');
      setToast({ message: 'Email и пароль успешно привязаны!', tone: 'ok' });
      haptic('success');
    } catch (err) {
      setToast({ message: err?.detail || 'Ошибка привязки email', tone: 'danger' });
      haptic('error');
    } finally {
      setLinkSaving(false);
    }
  };

  const handleGenerateTelegramLink = async () => {
    setGeneratingTgLink(true);
    try {
      const res = await api.generateTelegramLink(telegramId);
      if (res.bot_url) {
        window.open(res.bot_url, '_blank');
        setToast({ message: 'Открываем Telegram для подтверждения привязки...', tone: 'ok' });
      }
    } catch (err) {
      setToast({ message: err?.detail || 'Ошибка генерации ссылки', tone: 'danger' });
    } finally {
      setGeneratingTgLink(false);
    }
  };

  const isRealTelegram = telegramId && telegramId < 9000000000;

  if (loading && !contacts) {
    return <SkeletonList rows={3} />;
  }

  if (error && !contacts) {
    return <ErrorNote message={error} onRetry={loadProfile} retryLabel={t('common.retry')} />;
  }

  return (
    <div className="space-y-4">
      {/* Account & Authorization Card (Job Hunter style) */}
      <div className="card p-4 space-y-4 border-accent/30 bg-gradient-to-br from-card to-card-border/20">
        <div className="flex items-center justify-between border-b border-card-border/60 pb-3">
          <div className="flex items-center gap-3">
            <div className="h-11 w-11 rounded-full bg-accent/20 border border-accent/40 flex items-center justify-center text-accent text-lg font-bold">
              👤
            </div>
            <div>
              <p className="font-bold text-sm text-ink flex items-center gap-2">
                <span>{user?.email || (user?.username ? `@${user.username}` : t('profile.title'))}</span>
                {user?.has_password ? (
                  <span className="badge text-[10px] bg-emerald-500/20 text-emerald-400 font-semibold px-2 py-0.5">
                    ✓ Аккаунт активен
                  </span>
                ) : null}
              </p>
              <p className="text-xs text-muted font-mono mt-0.5">
                ID: {telegramId || '—'}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setAuthModalOpen(true)}
            className="badge border border-accent/50 bg-accent/10 text-accent font-semibold px-2.5 py-1 text-xs cursor-pointer hover:bg-accent/20 transition-colors"
          >
            Войти / Сменить
          </button>
        </div>

        {/* Bindings grid */}
        <div className="space-y-2 text-xs">
          {/* Email binding */}
          <div className="flex items-center justify-between p-2.5 rounded-lg bg-surface/50 border border-card-border/40">
            <div className="flex items-center gap-2">
              <span className="text-base">✉️</span>
              <div>
                <span className="font-semibold text-ink">Email: </span>
                <span className="text-muted">{user?.email || 'Не привязан'}</span>
              </div>
            </div>
            {user?.email ? (
              <span className="badge bg-emerald-500/20 text-emerald-400 font-semibold px-2 py-0.5 text-[10px]">
                ✓ Привязан
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setIsLinkingEmail(!isLinkingEmail)}
                className="badge border border-accent/40 bg-accent/15 text-accent font-semibold px-2 py-1 text-[11px] cursor-pointer hover:bg-accent/25"
              >
                + Привязать Email
              </button>
            )}
          </div>

          {/* Inline Email Linking Form */}
          {isLinkingEmail && !user?.email && (
            <form onSubmit={handleLinkEmail} className="p-3 rounded-lg bg-surface/80 border border-accent/30 space-y-2.5 mt-2">
              <p className="text-[11px] text-muted leading-relaxed">
                Задайте Email и пароль, чтобы входить в этот аккаунт с любого устройства без Telegram:
              </p>
              <input
                type="email"
                required
                placeholder="name@example.com"
                value={linkEmailVal}
                onChange={(e) => setLinkEmailVal(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded bg-bg-card border border-card-border text-ink text-xs"
              />
              <input
                type="password"
                required
                placeholder="Пароль (не менее 6 символов)"
                value={linkPassVal}
                onChange={(e) => setLinkPassVal(e.target.value)}
                className="w-full px-2.5 py-1.5 rounded bg-bg-card border border-card-border text-ink text-xs"
              />
              <div className="flex items-center gap-2 pt-1">
                <button
                  type="submit"
                  disabled={linkSaving}
                  className="btn-accent px-3 py-1 text-xs font-semibold cursor-pointer"
                >
                  {linkSaving ? 'Сохранение...' : 'Сохранить привязку'}
                </button>
                <button
                  type="button"
                  onClick={() => setIsLinkingEmail(false)}
                  className="text-xs text-muted hover:text-ink cursor-pointer px-2 py-1"
                >
                  Отмена
                </button>
              </div>
            </form>
          )}

          {/* Telegram binding */}
          <div className="flex items-center justify-between p-2.5 rounded-lg bg-surface/50 border border-card-border/40">
            <div className="flex items-center gap-2">
              <span className="text-base">✈️</span>
              <div>
                <span className="font-semibold text-ink">Telegram: </span>
                <span className="text-muted">
                  {user?.telegram_username ? `@${user.telegram_username}` : isRealTelegram ? 'Подключен' : 'Не привязан'}
                </span>
              </div>
            </div>
            {isRealTelegram || user?.telegram_username ? (
              <span className="badge bg-emerald-500/20 text-emerald-400 font-semibold px-2 py-0.5 text-[10px]">
                ✓ Привязан
              </span>
            ) : (
              <button
                type="button"
                onClick={handleGenerateTelegramLink}
                disabled={generatingTgLink}
                className="badge border border-accent/40 bg-accent/15 text-accent font-semibold px-2 py-1 text-[11px] cursor-pointer hover:bg-accent/25"
              >
                {generatingTgLink ? 'Секунду...' : '🔗 Привязать Telegram'}
              </button>
            )}
          </div>
        </div>

        {/* Logout action */}
        {user?.has_password || user?.email ? (
          <div className="pt-1 flex justify-end">
            <button
              type="button"
              onClick={() => {
                clearSessionUser();
                setToast({ message: 'Вы вышли из аккаунта', tone: 'ok' });
                window.location.reload();
              }}
              className="text-xs text-muted hover:text-rose-400 cursor-pointer transition-colors"
            >
              Выйти из профиля
            </button>
          </div>
        ) : null}
      </div>

      {/* Information Banner for WHOIS */}
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

              <div className="flex justify-between items-center py-1 border-b border-card-border/30">
                <span className="text-muted">{t('contact.address')}:</span>
                <span className="text-ink text-right">
                  {defaultContact.address_line1}, {defaultContact.city}, {defaultContact.country_code?.toUpperCase()}
                </span>
              </div>

              <div className="flex justify-between items-center py-1">
                <span className="text-muted">{t('contact.postal_code')}:</span>
                <span className="font-mono text-ink">{defaultContact.postal_code}</span>
              </div>
            </div>
          ) : (
            <div className="py-6 text-center space-y-3">
              <p className="text-xs text-muted">
                {t('profile.no_contact_yet')}
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

      {/* Auth Modal */}
      <AuthModal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        onSuccess={(u) => {
          setUser(u);
          loadProfile();
          setToast({ message: 'Авторизация выполнена успешно!', tone: 'ok' });
        }}
      />

      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </div>
  );
}
