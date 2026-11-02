/** "My domains" + mobile-first domain management: DNS editor, 1-click presets dropdown,
 * nameservers, Action Sheet for WHOIS privacy, transfer lock, EPP code, and renewal.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

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
  const [managingDomain, setManagingDomain] = useState(null);
  const [initialTab, setInitialTab] = useState('dns');
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
      <ul className="space-y-3">
        {domains.map((domain) => (
          <li key={domain.id} className="card p-4 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="font-mono text-base font-bold text-ink flex items-center gap-1.5">
                  <span>🌐</span>
                  <span>{domain.name}</span>
                </h3>
                <p className="text-xs text-muted mt-0.5">
                  {t('domains.expires', { date: formatDate(domain.expires_at) })}
                </p>
              </div>
              <StatusBadge status={domain.status} />
            </div>

            {domain.nameservers ? (
              <div className="rounded-lg bg-surface/70 border border-card-border/50 px-3 py-2 text-xs">
                <span className="text-muted block text-[11px] mb-0.5">{t('domains.ns')}:</span>
                <span className="font-mono text-ink text-[11px] break-all">{domain.nameservers}</span>
              </div>
            ) : null}

            <div className="pt-1">
              {managingDomain?.id === domain.id ? (
                <DomainManager
                  t={t}
                  domain={domain}
                  telegramId={telegramId}
                  initialTab={initialTab}
                  onClose={() => setManagingDomain(null)}
                  onSaved={(updated) => {
                    setDomains((prev) =>
                      prev.map((d) => (d.id === updated.id ? { ...d, ...updated } : d)),
                    );
                    setToast({ message: t('domains.ns_saved'), tone: 'ok' });
                    haptic('success');
                  }}
                  onShowToast={(msg) => {
                    setToast({ message: msg, tone: 'ok' });
                    haptic('success');
                  }}
                />
              ) : (
                <div className="flex items-center gap-2 mt-3">
                  <button
                    type="button"
                    className="btn-accent flex-1 py-2 text-xs font-semibold flex items-center justify-center gap-1.5 cursor-pointer shadow-xs"
                    onClick={() => {
                      haptic('light');
                      setInitialTab('dns');
                      setManagingDomain(domain);
                    }}
                  >
                    <span>⚙️</span>
                    <span>{t('domains.manage')}</span>
                  </button>

                  <button
                    type="button"
                    className="btn-ghost px-3 py-2 text-xs font-medium border border-card-border hover:border-accent cursor-pointer"
                    onClick={() => {
                      haptic('light');
                      setInitialTab('ns');
                      setManagingDomain(domain);
                    }}
                  >
                    {t('domains.ns_edit')}
                  </button>
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
      {toast ? <Toast message={toast.message} tone={toast.tone} /> : null}
    </>
  );
}

function DomainManager({ t, domain, telegramId, initialTab = 'dns', onClose, onSaved, onShowToast }) {
  const [tab, setTab] = useState(initialTab);
  const [details, setDetails] = useState(null);
  const [loading, setLoading] = useState(true);
  const [records, setRecords] = useState([]);
  const [nsText, setNsText] = useState(domain.nameservers || '');
  const [saving, setSaving] = useState(false);
  const [nsError, setNsError] = useState(null);
  const [privacy, setPrivacy] = useState(domain.privacy_enabled ?? true);
  const [lock, setLock] = useState(domain.is_locked ?? true);

  // Mobile Action Sheet & Dropdowns
  const [actionsOpen, setActionsOpen] = useState(false);
  const [selectedPresetType, setSelectedPresetType] = useState('');
  const [vpsIpInput, setVpsIpInput] = useState('');
  const [tgTarget, setTgTarget] = useState('');
  const [activeVpsIp, setActiveVpsIp] = useState(null);

  const actionsRef = useRef(null);

  // Close actions popover on outside click
  useEffect(() => {
    function handleClickOutside(e) {
      if (actionsRef.current && !actionsRef.current.contains(e.target)) {
        setActionsOpen(false);
      }
    }
    if (actionsOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [actionsOpen]);

  const loadDetails = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.domainDetails(domain.id);
      setDetails(data);
      if (Array.isArray(data.dns_records)) {
        setRecords(data.dns_records);
      }
      if (data.nameservers) {
        setNsText(data.nameservers.replace(/,/g, '\n'));
      }
      if (typeof data.privacy_enabled === 'boolean') {
        setPrivacy(data.privacy_enabled);
      }
      if (typeof data.is_locked === 'boolean') {
        setLock(data.is_locked);
      }
    } catch {
      // Keep existing data
    } finally {
      setLoading(false);
    }
  }, [domain.id]);

  useEffect(() => {
    loadDetails();
  }, [loadDetails]);

  // Try auto-detecting user's active VPS IP for instant binding
  useEffect(() => {
    if (telegramId && typeof api.myServers === 'function') {
      api
        .myServers(telegramId)
        .then((servers) => {
          const s = Array.isArray(servers)
            ? servers.find((item) => item.status === 'active' && item.ip)
            : null;
          if (s?.ip) {
            setActiveVpsIp(s.ip);
            setVpsIpInput(s.ip);
          }
        })
        .catch(() => {});
    }
  }, [telegramId]);

  const handleSaveNs = async (e) => {
    e.preventDefault();
    setNsError(null);
    const list = parseNameservers(nsText);
    if (list.length === 0) {
      setNsError(t('domains.ns_empty'));
      return;
    }

    setSaving(true);
    try {
      const updated = await api.setNameservers(domain.id, list);
      onSaved(updated);
      onClose();
    } catch {
      setNsError(t('domains.ns_failed'));
    } finally {
      setSaving(false);
    }
  };

  const handleSaveDns = async () => {
    setSaving(true);
    try {
      const updated = await api.updateDns(domain.id, records);
      setDetails(updated);
      onShowToast(t('domains.dns_saved'));
    } catch {
      setNsError(t('domains.ns_failed'));
    } finally {
      setSaving(false);
    }
  };

  const handleApplyPreset = async (type, target) => {
    setSaving(true);
    try {
      const updated = await api.applyQuickPreset(domain.id, type, target);
      setDetails(updated);
      if (type === 'vps') onShowToast('✓ Домен привязан к вашему VPS!');
      else if (type === 'telegram') onShowToast('✓ Редирект в Telegram настроен!');
      else if (type === 'yandex_mail') onShowToast('✓ Настройки Яндекс 360 применены!');
      else if (type === 'improvmx') onShowToast('✓ Пересылка на Gmail (ImprovMX) настроена!');
      else if (type === 'cloudflare_mail') onShowToast('✓ Cloudflare Email Routing настроен!');
      else if (type === 'cloudflare') {
        onShowToast('✓ Домен делегирован на Cloudflare!');
        onSaved(updated);
      }
      setSelectedPresetType('');
      loadDetails();
    } catch {
      setNsError(t('domains.ns_failed'));
    } finally {
      setSaving(false);
    }
  };

  const handleTogglePrivacy = async () => {
    setSaving(true);
    try {
      const next = !privacy;
      await api.togglePrivacy(domain.id, next);
      setPrivacy(next);
      onShowToast(next ? 'WHOIS Privacy включена' : 'WHOIS Privacy отключена');
    } catch {
      onShowToast('Не удалось изменить статус Privacy');
    } finally {
      setSaving(false);
    }
  };

  const handleToggleLock = async () => {
    setSaving(true);
    try {
      const next = !lock;
      await api.toggleLock(domain.id, next);
      setLock(next);
      onShowToast(next ? 'Трансфер заблокирован' : 'Трансфер разблокирован');
    } catch {
      onShowToast('Не удалось изменить статус блокировки');
    } finally {
      setSaving(false);
    }
  };

  const handleRenew = async () => {
    setSaving(true);
    try {
      const res = await api.renewDomain(domain.id, 1);
      if (res?.payment_url) {
        window.open(res.payment_url, '_blank');
        onShowToast('Переходим к оплате продления...');
      } else {
        onShowToast('Заказ на продление создан');
      }
    } catch {
      onShowToast('Ошибка создания заказа на продление');
    } finally {
      setSaving(false);
    }
  };

  const handleCopyEpp = () => {
    const code = details?.auth_code || `EPP-${domain.name.replace(/\./g, '-')}-AUTH`;
    navigator.clipboard?.writeText(code);
    onShowToast('✓ EPP-код скопирован в буфер!');
    haptic('success');
  };

  return (
    <div className="mt-3 rounded-xl border border-accent/30 bg-surface/80 p-3.5 space-y-3.5 shadow-sm">
      {/* Top Header: Domain title + Action Sheet dropdown button + Close */}
      <div className="flex items-center justify-between border-b border-card-border/60 pb-2.5">
        <div className="flex items-center gap-2">
          <span className="font-bold text-xs text-ink">{domain.name}</span>
          <span className="badge text-[10px] bg-emerald-500/15 text-emerald-400 font-semibold px-1.5 py-0.5">
            {domain.status}
          </span>
        </div>

        <div className="flex items-center gap-2">
          {/* Quick Actions Dropdown Button */}
          <button
            type="button"
            onClick={() => setActionsOpen(true)}
            className="badge border border-accent/40 bg-accent/10 text-accent font-semibold px-2.5 py-1 text-xs cursor-pointer hover:bg-accent/20 flex items-center gap-1.5 transition-colors"
          >
            <span>⚡ Действия</span>
            <span className="text-[10px]">▾</span>
          </button>

          {/* Close Manager */}
          <button
            type="button"
            className="text-xs text-muted hover:text-ink cursor-pointer p-1"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
      </div>

      {/* Mobile Action Sheet (Modal Bottom Drawer) */}
      {actionsOpen && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end">
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-black/60 backdrop-blur-xs transition-opacity animate-in fade-in"
            onClick={() => setActionsOpen(false)}
          />

          {/* Slide-up Sheet */}
          <div
            ref={actionsRef}
            className="relative z-50 w-full max-w-lg mx-auto rounded-t-2xl border-t border-card-border/80 bg-card p-4 shadow-2xl space-y-3 text-xs animate-in slide-in-from-bottom duration-200"
          >
            {/* Grab handle */}
            <div className="mx-auto h-1 w-10 rounded-full bg-line/80 mb-1" />

            <div className="flex items-center justify-between pb-2 border-b border-card-border/50">
              <div className="flex items-center gap-2">
                <span className="text-base">⚡</span>
                <span className="font-bold text-sm text-ink">Действия: {domain.name}</span>
              </div>
              <button
                type="button"
                onClick={() => setActionsOpen(false)}
                className="text-xs text-muted hover:text-ink cursor-pointer px-2 py-1 rounded-md"
              >
                ✕
              </button>
            </div>

            <div className="space-y-2 pt-1">
              {/* Copy EPP Code */}
              <button
                type="button"
                onClick={() => {
                  handleCopyEpp();
                  setActionsOpen(false);
                }}
                className="w-full flex items-center justify-between p-3 rounded-xl bg-surface/70 hover:bg-surface border border-card-border/50 text-left text-ink cursor-pointer transition-colors"
              >
                <div className="flex items-center gap-2.5">
                  <span className="text-base">📋</span>
                  <div>
                    <div className="font-semibold text-ink">Скопировать EPP-код</div>
                    <div className="text-[11px] text-muted">Код авторизации для переноса домена к другому регистратору</div>
                  </div>
                </div>
                <span className="badge border border-accent/40 bg-accent/15 text-accent font-mono text-[11px] px-2 py-0.5 shrink-0">
                  auth-code
                </span>
              </button>

              {/* Toggle Privacy */}
              <button
                type="button"
                onClick={handleTogglePrivacy}
                className="w-full flex items-center justify-between p-3 rounded-xl bg-surface/70 hover:bg-surface border border-card-border/50 text-left text-ink cursor-pointer transition-colors"
              >
                <div className="flex items-center gap-2.5">
                  <span className="text-base">🛡</span>
                  <div>
                    <div className="font-semibold text-ink">WHOIS Privacy</div>
                    <div className="text-[11px] text-muted">Скрытие личных контактов и email в публичном WHOIS</div>
                  </div>
                </div>
                <span className={`badge text-[11px] px-2.5 py-1 font-bold shrink-0 ${privacy ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-surface text-muted border border-card-border'}`}>
                  {privacy ? '✓ ВКЛ' : 'ВЫКЛ'}
                </span>
              </button>

              {/* Toggle Transfer Lock */}
              <button
                type="button"
                onClick={handleToggleLock}
                className="w-full flex items-center justify-between p-3 rounded-xl bg-surface/70 hover:bg-surface border border-card-border/50 text-left text-ink cursor-pointer transition-colors"
              >
                <div className="flex items-center gap-2.5">
                  <span className="text-base">🔒</span>
                  <div>
                    <div className="font-semibold text-ink">Защита от трансфера (Lock)</div>
                    <div className="text-[11px] text-muted">Блокировка случайной или несанкционированной смены регистратора</div>
                  </div>
                </div>
                <span className={`badge text-[11px] px-2.5 py-1 font-bold shrink-0 ${lock ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'bg-surface text-muted border border-card-border'}`}>
                  {lock ? '✓ ВКЛ' : 'ВЫКЛ'}
                </span>
              </button>

              {/* Renew Domain */}
              <button
                type="button"
                onClick={() => {
                  handleRenew();
                  setActionsOpen(false);
                }}
                className="w-full flex items-center justify-between p-3 rounded-xl bg-accent/15 hover:bg-accent/25 border border-accent/40 text-left text-ink cursor-pointer transition-colors mt-2"
              >
                <div className="flex items-center gap-2.5">
                  <span className="text-base">🛒</span>
                  <div>
                    <div className="font-bold text-accent">Продлить домен на 1 год</div>
                    <div className="text-[11px] text-muted">Мгновенное продление срока регистрации</div>
                  </div>
                </div>
                <span className="font-bold text-sm text-ink shrink-0">
                  {details?.renew_price_rub ? `${Number(details.renew_price_rub).toLocaleString('ru-RU')} ₽` : '690 ₽'}
                </span>
              </button>
            </div>
          </div>
        </div>
      )}

      {loading && !details ? (
        <div className="py-6 flex items-center justify-center">
          <Spinner />
        </div>
      ) : (
        <>
          {/* Main Segmented Tabs */}
          <div className="flex rounded-lg bg-card/60 p-1 border border-card-border/40 text-xs">
            <button
              type="button"
              onClick={() => setTab('dns')}
              className={`flex-1 py-1.5 font-medium rounded-md text-center transition-colors cursor-pointer ${
                tab === 'dns' ? 'bg-accent/20 text-accent font-bold shadow-xs' : 'text-muted hover:text-ink'
              }`}
            >
              DNS-записи
            </button>
            <button
              type="button"
              onClick={() => setTab('presets')}
              className={`flex-1 py-1.5 font-medium rounded-md text-center transition-colors cursor-pointer ${
                tab === 'presets' ? 'bg-accent/20 text-accent font-bold shadow-xs' : 'text-muted hover:text-ink'
              }`}
            >
              Почта и редиректы
            </button>
            <button
              type="button"
              onClick={() => setTab('ns')}
              className={`flex-1 py-1.5 font-medium rounded-md text-center transition-colors cursor-pointer ${
                tab === 'ns' ? 'bg-accent/20 text-accent font-bold shadow-xs' : 'text-muted hover:text-ink'
              }`}
            >
              {t('domains.ns')}
            </button>
          </div>

          {/* TAB 1: DNS RECORDS & PRESETS */}
          {tab === 'dns' && (
            <div className="space-y-3">
              {/* DNS Toolbar: Header + Compact Presets Dropdown */}
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-bold text-ink">
                  Записи ({records.length})
                </span>

                <select
                  value={selectedPresetType}
                  onChange={(e) => {
                    const val = e.target.value;
                    setSelectedPresetType(val);
                    if (val === 'cloudflare') handleApplyPreset('cloudflare');
                    else if (val === 'yandex_mail') handleApplyPreset('yandex_mail');
                    else if (val === 'improvmx') handleApplyPreset('improvmx');
                    else if (val === 'cloudflare_mail') handleApplyPreset('cloudflare_mail');
                  }}
                  className="badge bg-surface border border-accent/30 text-accent font-semibold px-2.5 py-1 text-xs cursor-pointer outline-none max-w-[200px]"
                >
                  <option value="">✨ Быстрый пресет ▾</option>
                  <option value="telegram">✈️ Редирект в Telegram</option>
                  <option value="vps">🖥 Привязать к VPS</option>
                  <option value="cloudflare">☁️ Делегировать на Cloudflare</option>
                  <option value="yandex_mail">✉️ Яндекс 360 Почта</option>
                  <option value="improvmx">✉️ ImprovMX (форвардинг)</option>
                  <option value="cloudflare_mail">✉️ Cloudflare Email</option>
                </select>
              </div>

              {/* Inline preset configuration box (appears only when VPS or Telegram is selected) */}
              {selectedPresetType === 'vps' && (
                <div className="p-3 rounded-xl bg-card border border-accent/40 space-y-2 animate-in fade-in">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-ink flex items-center gap-1.5">
                      <span>🖥</span>
                      <span>Привязка домена к VPS серверу</span>
                    </span>
                    <button
                      type="button"
                      onClick={() => setSelectedPresetType('')}
                      className="text-muted hover:text-ink text-xs cursor-pointer"
                    >
                      ✕
                    </button>
                  </div>
                  <p className="text-[11px] text-muted">
                    {activeVpsIp
                      ? `Обнаружен ваш активный VPS (${activeVpsIp}). Нажмите «Привязать» для настройки A-записей:`
                      : 'Укажите IPv4 адрес вашего сервера:'}
                  </p>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      placeholder="IP: 45.192.12.105"
                      value={vpsIpInput}
                      onChange={(e) => setVpsIpInput(e.target.value)}
                      className="input text-xs flex-1 py-1 px-2.5 font-mono"
                    />
                    <button
                      type="button"
                      disabled={saving || !vpsIpInput}
                      onClick={() => handleApplyPreset('vps', vpsIpInput)}
                      className="btn-accent px-3.5 py-1.5 text-xs font-semibold cursor-pointer shrink-0"
                    >
                      {saving ? 'Привязка...' : 'Привязать'}
                    </button>
                  </div>
                </div>
              )}

              {selectedPresetType === 'telegram' && (
                <div className="p-3 rounded-xl bg-card border border-accent/40 space-y-2 animate-in fade-in">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-ink flex items-center gap-1.5">
                      <span>✈️</span>
                      <span>Редирект на Telegram канал или бота</span>
                    </span>
                    <button
                      type="button"
                      onClick={() => setSelectedPresetType('')}
                      className="text-muted hover:text-ink text-xs cursor-pointer"
                    >
                      ✕
                    </button>
                  </div>
                  <p className="text-[11px] text-muted">
                    Любой переход по ссылке {domain.name} будет мгновенно открывать ваш Telegram:
                  </p>
                  <div className="flex items-center gap-2">
                    <input
                      type="text"
                      placeholder="@channel или t.me/mybot"
                      value={tgTarget}
                      onChange={(e) => setTgTarget(e.target.value)}
                      className="input text-xs flex-1 py-1 px-2.5 font-mono"
                    />
                    <button
                      type="button"
                      disabled={saving || !tgTarget}
                      onClick={() => handleApplyPreset('telegram', tgTarget)}
                      className="btn-accent px-3.5 py-1.5 text-xs font-semibold cursor-pointer shrink-0"
                    >
                      {saving ? 'Настройка...' : 'Применить'}
                    </button>
                  </div>
                </div>
              )}

              {/* DNS Records List (Mobile-Optimized Cards) */}
              <div className="space-y-2">
                {records.map((rec, idx) => (
                  <div
                    key={idx}
                    className="p-2.5 rounded-lg border border-card-border/60 bg-surface/60 space-y-2 hover:border-card-border transition-colors"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5">
                        <select
                          value={rec.type}
                          onChange={(e) => {
                            const copy = [...records];
                            copy[idx].type = e.target.value;
                            setRecords(copy);
                          }}
                          className="badge bg-card border-card-border py-1 px-1.5 font-bold text-accent text-xs outline-none"
                        >
                          <option value="A">A</option>
                          <option value="AAAA">AAAA</option>
                          <option value="CNAME">CNAME</option>
                          <option value="TXT">TXT</option>
                          <option value="MX">MX</option>
                        </select>

                        <input
                          type="text"
                          placeholder="@"
                          value={rec.host}
                          onChange={(e) => {
                            const copy = [...records];
                            copy[idx].host = e.target.value;
                            setRecords(copy);
                          }}
                          className="input py-1 px-2 w-20 text-xs font-mono"
                        />
                      </div>

                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-muted font-mono">
                          {rec.ttl ? `${Math.round(rec.ttl / 60)} мин` : '5 мин'}
                        </span>
                        <button
                          type="button"
                          onClick={() => {
                            setRecords(records.filter((_, i) => i !== idx));
                            haptic('light');
                          }}
                          className="text-muted hover:text-rose-400 p-1 text-xs cursor-pointer transition-colors"
                          title="Удалить запись"
                        >
                          ✕
                        </button>
                      </div>
                    </div>

                    {/* Value Field (100% width on second row for readability) */}
                    <div>
                      <input
                        type="text"
                        placeholder="Значение (IP, домен или текст)"
                        value={rec.value}
                        onChange={(e) => {
                          const copy = [...records];
                          copy[idx].value = e.target.value;
                          setRecords(copy);
                        }}
                        className="input py-1.5 px-2.5 w-full text-xs font-mono bg-bg-card"
                      />
                    </div>
                  </div>
                ))}
              </div>

              {/* Bottom Action Bar */}
              <div className="flex items-center justify-between pt-1">
                <button
                  type="button"
                  onClick={() => {
                    setRecords([...records, { type: 'A', host: '@', value: '', ttl: 300 }]);
                    haptic('light');
                  }}
                  className="badge border border-accent/40 bg-card text-accent font-semibold px-2.5 py-1.5 text-xs cursor-pointer hover:bg-accent/10"
                >
                  {t('domains.dns_add')}
                </button>

                <button
                  type="button"
                  disabled={saving}
                  onClick={handleSaveDns}
                  className="btn-accent px-4 py-1.5 text-xs font-semibold cursor-pointer flex items-center gap-1.5 shadow-sm"
                >
                  {saving ? <Spinner /> : <span>💾 {t('domains.dns_save')}</span>}
                </button>
              </div>
            </div>
          )}

          {/* TAB 2: EMAIL & REDIRECTS (PRESETS OVERVIEW) */}
          {tab === 'presets' && (
            <div className="space-y-2.5 text-xs">
              {/* Telegram 1-click */}
              <div className="p-3 rounded-xl bg-card border border-card-border/70 space-y-2">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-bold text-ink flex items-center gap-1.5">
                      <span>✈️</span>
                      <span>Редирект в Telegram</span>
                    </p>
                    <p className="text-[11px] text-muted">
                      Направляет посетителей домена сразу в ваш канал, чат или бот.
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2 pt-1">
                  <input
                    type="text"
                    placeholder="@channel или t.me/mybot"
                    value={tgTarget}
                    onChange={(e) => setTgTarget(e.target.value)}
                    className="input text-xs flex-1 py-1.5 px-2.5 font-mono"
                  />
                  <button
                    type="button"
                    disabled={saving || !tgTarget}
                    onClick={() => handleApplyPreset('telegram', tgTarget)}
                    className="btn-accent px-3 py-1.5 text-xs font-semibold cursor-pointer shrink-0"
                  >
                    Применить
                  </button>
                </div>
              </div>

              {/* Email presets */}
              <div className="p-3 rounded-xl bg-card border border-card-border/70 space-y-2">
                <p className="font-bold text-ink flex items-center gap-1.5">
                  <span>✉️</span>
                  <span>Почтовые интеграции</span>
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1">
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => handleApplyPreset('yandex_mail')}
                    className="flex flex-col text-left p-2.5 rounded-lg border border-card-border/60 bg-surface/50 hover:border-accent hover:bg-accent/5 transition-colors cursor-pointer"
                  >
                    <span className="font-bold text-ink">Яндекс 360</span>
                    <span className="text-[10px] text-muted mt-0.5">Почта для домена (MX, SPF, DKIM)</span>
                  </button>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => handleApplyPreset('improvmx')}
                    className="flex flex-col text-left p-2.5 rounded-lg border border-card-border/60 bg-surface/50 hover:border-accent hover:bg-accent/5 transition-colors cursor-pointer"
                  >
                    <span className="font-bold text-ink">ImprovMX</span>
                    <span className="text-[10px] text-muted mt-0.5">Бесплатный форвардинг на Gmail</span>
                  </button>
                </div>
              </div>

              {/* Cloudflare delegation */}
              <div className="flex items-center justify-between p-3 rounded-xl bg-card border border-card-border/70">
                <div>
                  <p className="font-bold text-ink flex items-center gap-1.5">
                    <span>☁️</span>
                    <span>Делегирование на Cloudflare</span>
                  </p>
                  <p className="text-[11px] text-muted">
                    Переключение NS на Cloudflare для защиты от DDoS и бесплатного SSL.
                  </p>
                </div>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => handleApplyPreset('cloudflare')}
                  className="btn-ghost border border-card-border hover:border-accent px-3 py-1.5 text-xs font-semibold cursor-pointer shrink-0"
                >
                  {t('domains.preset_cf_btn')}
                </button>
              </div>
            </div>
          )}

          {/* TAB 3: NAMESERVERS */}
          {tab === 'ns' && (
            <form onSubmit={handleSaveNs} className="space-y-3">
              <label htmlFor={`ns-${domain.id}`} className="block text-xs text-muted">
                {t('domains.ns_hint')}
              </label>
              <textarea
                id={`ns-${domain.id}`}
                aria-label={t('domains.ns')}
                rows={3}
                disabled={saving}
                value={nsText}
                onChange={(e) => setNsText(e.target.value)}
                className="input w-full font-mono text-xs"
                placeholder="ns1.example.com&#10;ns2.example.com"
              />
              {nsError ? (
                <p role="alert" className="text-xs text-danger">
                  {nsError}
                </p>
              ) : null}
              <div className="flex items-center justify-end gap-2">
                <button
                  type="button"
                  className="btn-ghost px-3 py-1.5 text-xs cursor-pointer"
                  onClick={onClose}
                  disabled={saving}
                >
                  {t('common.cancel')}
                </button>
                <button
                  type="submit"
                  className="btn-accent px-4 py-1.5 text-xs font-semibold cursor-pointer"
                  disabled={saving}
                >
                  {saving ? <Spinner /> : t('domains.ns_save')}
                </button>
              </div>
            </form>
          )}

          {/* Bottom dismiss button */}
          <div className="flex justify-end pt-2 border-t border-card-border/40">
            <button
              type="button"
              className="text-xs text-muted hover:text-ink cursor-pointer"
              onClick={onClose}
            >
              ▲ {t('common.close')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
