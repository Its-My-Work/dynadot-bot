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

  // Instantly react to auth changes (login, register, switch account) without page reload
  useEffect(() => {
    function onAuthChanged(e) {
      if (e.detail) {
        setUser(e.detail);
      }
      loadProfile();
    }
    window.addEventListener('dynadot_auth_changed', onAuthChanged);
    return () => window.removeEventListener('dynadot_auth_changed', onAuthChanged);
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
  const isAdmin = user?.role === 'admin';

  if (loading && !contacts) {
    return <SkeletonList rows={3} />;
  }

  if (error && !contacts) {
    return <ErrorNote message={error} onRetry={loadProfile} retryLabel={t('common.retry')} />;
  }

  return (
    <div className="space-y-4">
      {/* Account & Authorization Card */}
      <div className="card p-5 space-y-4 border-card-border/80 shadow-md">
        <div className="flex items-center justify-between border-b border-card-border/50 pb-3">
          <div className="flex items-center gap-3">
            <div className="h-11 w-11 rounded-full bg-accent/15 border border-accent/30 flex items-center justify-center text-accent text-lg font-bold">
              {isAdmin ? '👑' : '👤'}
            </div>
            <div>
              <p className="font-semibold text-sm text-ink flex items-center gap-2">
                <span>{user?.email || (user?.username ? `@${user.username}` : t('profile.title'))}</span>
                {isAdmin ? (
                  <span className="badge text-[10px] bg-amber-500/10 text-amber-800 dark:text-amber-300 border border-amber-500/30 font-medium px-2 py-0.5">
                    Администратор
                  </span>
                ) : user?.has_password ? (
                  <span className="badge text-[10px] bg-surface text-ink border border-line font-medium px-2 py-0.5 inline-flex items-center gap-1.5 shadow-2xs">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                    <span>Активен</span>
                  </span>
                ) : null}
              </p>
              <p className="text-xs text-muted font-normal mt-0.5">
                {user?.email ? 'Основной аккаунт' : `Сессия: ID ${telegramId}`}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setAuthModalOpen(true)}
            className="badge border border-accent/40 bg-accent/10 text-accent font-medium px-3 py-1.5 text-xs cursor-pointer hover:bg-accent/20 transition-colors"
          >
            Войти / Сменить
          </button>
        </div>

        {/* Bindings list */}
        <div className="space-y-2.5 text-xs">
          {/* Email binding */}
          <div className="flex items-center justify-between p-3 rounded-xl bg-surface/70 border border-line">
            <div className="flex items-center gap-2.5">
              <span className="text-base">✉️</span>
              <div>
                <div className="font-medium text-ink">Email</div>
                <div className="text-muted text-[11px]">{user?.email || 'Не привязан'}</div>
              </div>
            </div>
            {user?.email ? (
              <span className="badge bg-surface text-ink border border-line font-medium px-2 py-0.5 text-[11px] inline-flex items-center gap-1.5 shadow-2xs">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                <span>Привязан</span>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setIsLinkingEmail(!isLinkingEmail)}
                className="badge border border-accent/40 bg-accent/15 text-accent font-medium px-2.5 py-1 text-[11px] cursor-pointer hover:bg-accent/25 transition-colors"
              >
                + Привязать Email
              </button>
            )}
          </div>

          {/* Inline Email Linking Form */}
          {isLinkingEmail && !user?.email && (
            <form onSubmit={handleLinkEmail} className="p-3.5 rounded-xl bg-surface border border-accent/30 space-y-2.5">
              <p className="text-[11px] text-muted leading-relaxed">
                Задайте Email и пароль, чтобы иметь доступ к доменам и серверам с любого браузера без Telegram:
              </p>
              <input
                type="email"
                required
                placeholder="name@example.com"
                value={linkEmailVal}
                onChange={(e) => setLinkEmailVal(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-bg-card border border-card-border text-ink text-xs focus:border-accent outline-none"
              />
              <input
                type="password"
                required
                placeholder="Пароль (не менее 6 символов)"
                value={linkPassVal}
                onChange={(e) => setLinkPassVal(e.target.value)}
                className="w-full px-3 py-2 rounded-lg bg-bg-card border border-card-border text-ink text-xs focus:border-accent outline-none"
              />
              <div className="flex items-center gap-2 pt-1">
                <button
                  type="submit"
                  disabled={linkSaving}
                  className="btn-accent px-3 py-1.5 text-xs font-semibold cursor-pointer"
                >
                  {linkSaving ? 'Сохранение...' : 'Сохранить привязку'}
                </button>
                <button
                  type="button"
                  onClick={() => setIsLinkingEmail(false)}
                  className="text-xs text-muted hover:text-ink cursor-pointer px-2 py-1.5"
                >
                  Отмена
                </button>
              </div>
            </form>
          )}

          {/* Telegram binding */}
          <div className="flex items-center justify-between p-3 rounded-xl bg-surface/70 border border-line">
            <div className="flex items-center gap-2.5">
              <span className="text-base">✈️</span>
              <div>
                <div className="font-medium text-ink">Telegram</div>
                <div className="text-muted text-[11px]">
                  {user?.telegram_username ? `@${user.telegram_username}` : isRealTelegram ? 'Подключен через бота' : 'Не привязан'}
                </div>
              </div>
            </div>
            {isRealTelegram || user?.telegram_username ? (
              <span className="badge bg-surface text-ink border border-line font-medium px-2 py-0.5 text-[11px] inline-flex items-center gap-1.5 shadow-2xs">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                <span>Привязан</span>
              </span>
            ) : (
              <button
                type="button"
                onClick={handleGenerateTelegramLink}
                disabled={generatingTgLink}
                className="badge border border-accent/40 bg-accent/15 text-accent font-medium px-2.5 py-1 text-[11px] cursor-pointer hover:bg-accent/25 transition-colors"
              >
                {generatingTgLink ? 'Секунду...' : '🔗 Привязать Telegram'}
              </button>
            )}
          </div>
        </div>

        {/* Footer actions (Admin portal link + Logout) */}
        <div className="pt-2 flex items-center justify-between border-t border-line">
          {isAdmin ? (
            <a
              href="/admin.html"
              className="text-xs font-medium text-amber-800 dark:text-amber-300 hover:underline flex items-center gap-1 transition-colors"
            >
              <span>⚙️</span>
              <span>Панель управления (Админка) →</span>
            </a>
          ) : <div />}

          {user?.has_password || user?.email ? (
            <button
              type="button"
              onClick={() => {
                clearSessionUser();
                setToast({ message: 'Вы вышли из профиля', tone: 'ok' });
                window.location.reload();
              }}
              className="text-xs text-muted hover:text-rose-400 cursor-pointer transition-colors"
            >
              Выйти из профиля
            </button>
          ) : null}
        </div>
      </div>

      {/* Information Banner for WHOIS */}
      <div className="rounded-xl border border-accent/25 bg-accent/5 p-3.5 space-y-1">
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
          <div className="flex items-center justify-between border-b border-line pb-2">
            <h3 className="text-sm font-semibold text-ink">
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
        <div className="card p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-line pb-3">
            <h3 className="text-sm font-semibold text-ink flex items-center gap-2">
              <span>📋</span>
              <span>{t('profile.owner_data')}</span>
            </h3>
            <button
              type="button"
              onClick={() => {
                haptic('light');
                setEditing(true);
              }}
              className="badge border border-accent/40 bg-surface text-accent font-medium px-2.5 py-1 text-xs cursor-pointer hover:bg-accent/10"
            >
              {defaultContact ? t('profile.edit_btn') : t('profile.create_btn')}
            </button>
          </div>

          {defaultContact ? (
            <div className="space-y-2.5 text-xs">
              <div className="flex justify-between items-center py-1.5 border-b border-line">
                <span className="text-muted">{t('contact.first_name')} / {t('contact.last_name')}:</span>
                <span className="font-medium text-ink">
                  {defaultContact.first_name} {defaultContact.last_name}
                </span>
              </div>

              <div className="flex justify-between items-center py-1.5 border-b border-line">
                <span className="text-muted">{t('contact.email')}:</span>
                <span className="font-mono text-ink">{defaultContact.email}</span>
              </div>

              <div className="flex justify-between items-center py-1.5 border-b border-line">
                <span className="text-muted">{t('contact.phone')}:</span>
                <span className="font-mono text-ink">
                  +{defaultContact.phone_country_code} {defaultContact.phone_number}
                </span>
              </div>

              <div className="flex justify-between items-center py-1.5 border-b border-line">
                <span className="text-muted">{t('contact.address')}:</span>
                <span className="text-ink text-right">
                  {defaultContact.address_line1}, {defaultContact.city}, {defaultContact.country_code?.toUpperCase()}
                </span>
              </div>

              <div className="flex justify-between items-center py-1.5">
                <span className="text-muted">{t('contact.postal_code')}:</span>
                <span className="font-mono text-ink">{defaultContact.postal_code}</span>
              </div>
            </div>
          ) : (
            <div className="py-6 px-4 text-center space-y-3 rounded-xl bg-surface/50 border border-dashed border-line">
              <div className="text-2xl">📇</div>
              <p className="text-xs text-muted max-w-sm mx-auto leading-relaxed">
                {t('profile.no_contact_yet')}
              </p>
              <button
                type="button"
                onClick={() => {
                  haptic('light');
                  setEditing(true);
                }}
                className="btn-accent px-4 py-2 text-xs font-medium cursor-pointer shadow-sm"
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
