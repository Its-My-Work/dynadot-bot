import { useState } from 'react';
import api from '../lib/api';
import { saveSessionUser } from '../lib/telegram';

export default function AuthModal({ isOpen, onClose, onSuccess, initialTab = 'email' }) {
  const [tab, setTab] = useState(initialTab); // 'email' | 'telegram'
  const [isRegister, setIsRegister] = useState(false);

  // Email form state
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  // Telegram form state
  const [tgUsername, setTgUsername] = useState('');
  const [tgCode, setTgCode] = useState('');
  const [codeSent, setCodeSent] = useState(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [infoMessage, setInfoMessage] = useState(null);

  if (!isOpen) return null;

  function resetState() {
    setError(null);
    setInfoMessage(null);
    setLoading(false);
  }

  async function handleEmailSubmit(e) {
    e.preventDefault();
    resetState();
    if (!email || !password) {
      setError('Заполните email и пароль');
      return;
    }
    setLoading(true);
    try {
      const res = isRegister
        ? await api.registerEmail(email, password)
        : await api.loginEmail(email, password);

      saveSessionUser(res.user);
      if (onSuccess) onSuccess(res.user);
      onClose();
    } catch (err) {
      setError(err?.detail || err?.message || 'Ошибка авторизации');
    } finally {
      setLoading(false);
    }
  }

  async function handleRequestTelegramCode(e) {
    e.preventDefault();
    resetState();
    if (!tgUsername.trim()) {
      setError('Введите ваш ник в Telegram');
      return;
    }
    setLoading(true);
    try {
      const res = await api.requestTelegramCode(tgUsername);
      setCodeSent(true);
      setInfoMessage(res.message || 'Код отправлен в личные сообщения бота @itsmywork_ai_bot');
    } catch (err) {
      setError(err?.detail || err?.message || 'Пользователь не найден');
    } finally {
      setLoading(false);
    }
  }

  async function handleVerifyTelegramCode(e) {
    e.preventDefault();
    resetState();
    if (!tgCode.trim()) {
      setError('Введите 6-значный проверочный код');
      return;
    }
    setLoading(true);
    try {
      const res = await api.verifyTelegramCode(tgUsername, tgCode);
      saveSessionUser(res.user);
      if (onSuccess) onSuccess(res.user);
      onClose();
    } catch (err) {
      setError(err?.detail || err?.message || 'Неверный код подтверждения');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(4px)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px',
      }}
      onClick={onClose}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '420px',
          backgroundColor: 'var(--color-bg-card, #1c1d22)',
          border: '1px solid var(--color-border, #2d3039)',
          borderRadius: '16px',
          boxShadow: '0 20px 40px rgba(0,0,0,0.5)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: '18px 20px',
            borderBottom: '1px solid var(--color-border, #2d3039)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ fontWeight: 700, fontSize: '18px', color: 'var(--color-text-primary, #fff)' }}>
            Вход в аккаунт
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--color-text-secondary, #9aa0a6)',
              fontSize: '20px',
              cursor: 'pointer',
              padding: '4px 8px',
            }}
          >
            ✕
          </button>
        </div>

        {/* Tab switchers */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            borderBottom: '1px solid var(--color-border, #2d3039)',
            background: 'var(--color-bg-secondary, #14151a)',
          }}
        >
          <button
            type="button"
            onClick={() => {
              setTab('email');
              resetState();
            }}
            style={{
              padding: '12px',
              border: 'none',
              background: tab === 'email' ? 'var(--color-bg-card, #1c1d22)' : 'transparent',
              color: tab === 'email' ? 'var(--color-accent, #3b82f6)' : 'var(--color-text-secondary, #9aa0a6)',
              fontWeight: tab === 'email' ? 700 : 500,
              borderBottom: tab === 'email' ? '2px solid var(--color-accent, #3b82f6)' : 'none',
              cursor: 'pointer',
              fontSize: '14px',
            }}
          >
            ✉️ По Email
          </button>
          <button
            type="button"
            onClick={() => {
              setTab('telegram');
              resetState();
            }}
            style={{
              padding: '12px',
              border: 'none',
              background: tab === 'telegram' ? 'var(--color-bg-card, #1c1d22)' : 'transparent',
              color: tab === 'telegram' ? 'var(--color-accent, #3b82f6)' : 'var(--color-text-secondary, #9aa0a6)',
              fontWeight: tab === 'telegram' ? 700 : 500,
              borderBottom: tab === 'telegram' ? '2px solid var(--color-accent, #3b82f6)' : 'none',
              cursor: 'pointer',
              fontSize: '14px',
            }}
          >
            ✈️ Через Telegram
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: '20px' }}>
          {error && (
            <div
              style={{
                padding: '10px 14px',
                borderRadius: '8px',
                background: 'rgba(239, 68, 68, 0.15)',
                color: '#f87171',
                fontSize: '13px',
                marginBottom: '16px',
                border: '1px solid rgba(239, 68, 68, 0.3)',
              }}
            >
              {error}
            </div>
          )}

          {infoMessage && (
            <div
              style={{
                padding: '10px 14px',
                borderRadius: '8px',
                background: 'rgba(34, 197, 94, 0.15)',
                color: '#4ade80',
                fontSize: '13px',
                marginBottom: '16px',
                border: '1px solid rgba(34, 197, 94, 0.3)',
              }}
            >
              {infoMessage}
            </div>
          )}

          {tab === 'email' && (
            <form onSubmit={handleEmailSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
              <div>
                <label
                  style={{
                    display: 'block',
                    fontSize: '12px',
                    fontWeight: 600,
                    color: 'var(--color-text-secondary, #9aa0a6)',
                    marginBottom: '6px',
                  }}
                >
                  Адрес электронной почты
                </label>
                <input
                  type="email"
                  required
                  placeholder="name@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    borderRadius: '8px',
                    border: '1px solid var(--color-border, #2d3039)',
                    background: 'var(--color-bg-input, #121316)',
                    color: '#fff',
                    fontSize: '14px',
                    boxSizing: 'border-box',
                  }}
                />
              </div>

              <div>
                <label
                  style={{
                    display: 'block',
                    fontSize: '12px',
                    fontWeight: 600,
                    color: 'var(--color-text-secondary, #9aa0a6)',
                    marginBottom: '6px',
                  }}
                >
                  Пароль {isRegister && '(не менее 6 символов)'}
                </label>
                <input
                  type="password"
                  required
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '10px 12px',
                    borderRadius: '8px',
                    border: '1px solid var(--color-border, #2d3039)',
                    background: 'var(--color-bg-input, #121316)',
                    color: '#fff',
                    fontSize: '14px',
                    boxSizing: 'border-box',
                  }}
                />
              </div>

              <button
                type="submit"
                disabled={loading}
                style={{
                  padding: '12px',
                  borderRadius: '8px',
                  border: 'none',
                  background: 'var(--color-accent, #3b82f6)',
                  color: '#fff',
                  fontWeight: 600,
                  fontSize: '15px',
                  cursor: loading ? 'default' : 'pointer',
                  opacity: loading ? 0.7 : 1,
                  marginTop: '6px',
                }}
              >
                {loading ? 'Секунду...' : isRegister ? 'Зарегистрироваться' : 'Войти'}
              </button>

              <div style={{ textAlign: 'center', marginTop: '6px' }}>
                <button
                  type="button"
                  onClick={() => {
                    setIsRegister(!isRegister);
                    resetState();
                  }}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--color-text-secondary, #9aa0a6)',
                    fontSize: '13px',
                    cursor: 'pointer',
                    textDecoration: 'underline',
                  }}
                >
                  {isRegister ? 'Уже есть аккаунт? Войти' : 'Нет аккаунта? Зарегистрироваться'}
                </button>
              </div>
            </form>
          )}

          {tab === 'telegram' && (
            <div>
              {!codeSent ? (
                <form
                  onSubmit={handleRequestTelegramCode}
                  style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}
                >
                  <p
                    style={{
                      fontSize: '13px',
                      color: 'var(--color-text-secondary, #9aa0a6)',
                      lineHeight: '1.4',
                      margin: 0,
                    }}
                  >
                    Введите ник в Telegram. Наш бот{' '}
                    <b style={{ color: '#fff' }}>@itsmywork_ai_bot</b> пришлёт одноразовый 6-значный код для входа.
                  </p>

                  <div>
                    <label
                      style={{
                        display: 'block',
                        fontSize: '12px',
                        fontWeight: 600,
                        color: 'var(--color-text-secondary, #9aa0a6)',
                        marginBottom: '6px',
                      }}
                    >
                      Ваш Telegram username
                    </label>
                    <div style={{ display: 'flex', alignItems: 'center' }}>
                      <span
                        style={{
                          padding: '10px 12px',
                          background: 'var(--color-bg-secondary, #14151a)',
                          border: '1px solid var(--color-border, #2d3039)',
                          borderRight: 'none',
                          borderTopLeftRadius: '8px',
                          borderBottomLeftRadius: '8px',
                          color: 'var(--color-text-secondary, #9aa0a6)',
                        }}
                      >
                        @
                      </span>
                      <input
                        type="text"
                        required
                        placeholder="username"
                        value={tgUsername}
                        onChange={(e) => setTgUsername(e.target.value.replace(/^@/, ''))}
                        style={{
                          flex: 1,
                          padding: '10px 12px',
                          borderTopRightRadius: '8px',
                          borderBottomRightRadius: '8px',
                          border: '1px solid var(--color-border, #2d3039)',
                          background: 'var(--color-bg-input, #121316)',
                          color: '#fff',
                          fontSize: '14px',
                          boxSizing: 'border-box',
                        }}
                      />
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={loading}
                    style={{
                      padding: '12px',
                      borderRadius: '8px',
                      border: 'none',
                      background: 'var(--color-accent, #3b82f6)',
                      color: '#fff',
                      fontWeight: 600,
                      fontSize: '15px',
                      cursor: loading ? 'default' : 'pointer',
                      opacity: loading ? 0.7 : 1,
                    }}
                  >
                    {loading ? 'Отправляем...' : 'Получить код в Telegram'}
                  </button>
                </form>
              ) : (
                <form
                  onSubmit={handleVerifyTelegramCode}
                  style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}
                >
                  <div>
                    <label
                      style={{
                        display: 'block',
                        fontSize: '12px',
                        fontWeight: 600,
                        color: 'var(--color-text-secondary, #9aa0a6)',
                        marginBottom: '6px',
                      }}
                    >
                      6-значный проверочный код
                    </label>
                    <input
                      type="text"
                      required
                      placeholder="123456"
                      maxLength={8}
                      value={tgCode}
                      onChange={(e) => setTgCode(e.target.value.trim())}
                      style={{
                        width: '100%',
                        padding: '12px',
                        textAlign: 'center',
                        letterSpacing: '4px',
                        fontWeight: 700,
                        fontSize: '20px',
                        borderRadius: '8px',
                        border: '1px solid var(--color-border, #2d3039)',
                        background: 'var(--color-bg-input, #121316)',
                        color: '#fff',
                        boxSizing: 'border-box',
                      }}
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={loading}
                    style={{
                      padding: '12px',
                      borderRadius: '8px',
                      border: 'none',
                      background: 'var(--color-accent, #3b82f6)',
                      color: '#fff',
                      fontWeight: 600,
                      fontSize: '15px',
                      cursor: loading ? 'default' : 'pointer',
                      opacity: loading ? 0.7 : 1,
                    }}
                  >
                    {loading ? 'Проверяем...' : 'Подтвердить и войти'}
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setCodeSent(false);
                      setTgCode('');
                      resetState();
                    }}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      color: 'var(--color-text-secondary, #9aa0a6)',
                      fontSize: '13px',
                      cursor: 'pointer',
                    }}
                  >
                    ← Изменить ник или отправить повторно
                  </button>
                </form>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
