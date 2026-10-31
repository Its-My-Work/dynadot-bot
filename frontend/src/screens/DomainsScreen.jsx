/** "My domains" + full domain management: DNS editor, 1-click presets,
 * nameservers, WHOIS privacy, transfer lock, and renewal.
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
  const [managingDomain, setManagingDomain] = useState(null);
  const [initialTab, setInitialTab] = useState('presets');
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
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="truncate font-bold text-base text-ink">{domain.name}</p>
                  <span className="badge text-[10px] px-1.5 py-0.5 border border-accent/30 text-accent font-semibold">
                    .{domain.tld}
                  </span>
                </div>
                <p className="text-xs text-muted mt-0.5">
                  {t('domains.expires', {
                    date: formatDate(domain.expires_at, t.language),
                  })}
                </p>
              </div>
              <StatusBadge
                status={domain.status}
                label={
                  t(`status.${domain.status}`) === `status.${domain.status}`
                    ? domain.status
                    : t(`status.${domain.status}`)
                }
              />
            </div>

            <div className="border-t border-card-border/60 pt-2.5">
              <div className="flex items-center justify-between text-xs text-muted mb-1">
                <span className="font-medium">{t('domains.ns')}:</span>
                <span className="font-mono text-ink truncate max-w-[200px]">
                  {domain.nameservers || 'Dynadot Default DNS'}
                </span>
              </div>

              {managingDomain?.id === domain.id ? (
                <DomainManager
                  t={t}
                  domain={domain}
                  telegramId={telegramId}
                  initialTab={initialTab}
                  onClose={() => setManagingDomain(null)}
                  onSaved={(updated) => {
                    setDomains((current) =>
                      current.map((d) => (d.id === updated.id ? { ...d, ...updated } : d)),
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
                      setInitialTab('presets');
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

function DomainManager({ t, domain, telegramId, initialTab = 'presets', onClose, onSaved, onShowToast }) {
  const [tab, setTab] = useState(initialTab);
  const [details, setDetails] = useState(null);
  const [loading, setLoading] = useState(true);
  const [servers, setServers] = useState([]);
  const [saving, setSaving] = useState(false);

  // NS state
  const [nsText, setNsText] = useState((domain.nameservers || '').split(',').join('\n'));
  const [nsError, setNsError] = useState(null);

  // DNS records state
  const [records, setRecords] = useState([]);

  // Presets state
  const [tgTarget, setTgTarget] = useState('');
  const [vpsIpInput, setVpsIpInput] = useState('');

  // Security state
  const [privacy, setPrivacy] = useState(domain.privacy_enabled ?? true);
  const [isLocked, setIsLocked] = useState(domain.is_locked ?? true);
  const [showEpp, setShowEpp] = useState(false);

  const loadDetails = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.domainDetails(domain.id);
      setDetails(data);
      if (data.nameservers) {
        setNsText(data.nameservers.split(',').join('\n'));
      }
      if (Array.isArray(data.dns_records)) {
        setRecords(data.dns_records);
      }
      if (data.privacy_enabled !== undefined) setPrivacy(data.privacy_enabled);
      if (data.is_locked !== undefined) setIsLocked(data.is_locked);
    } catch {
      setRecords([
        { type: 'A', host: '@', value: '76.76.21.21', ttl: 300 },
        { type: 'CNAME', host: 'www', value: domain.name, ttl: 300 },
      ]);
    }

    try {
      if (telegramId) {
        const sList = await api.myServers(telegramId);
        if (Array.isArray(sList)) setServers(sList);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [domain.id, domain.name, telegramId]);

  useEffect(() => {
    loadDetails();
  }, [loadDetails]);

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
      else if (type === 'cloudflare') {
        onShowToast('✓ Домен делегирован на Cloudflare!');
        onSaved(updated);
      }
      loadDetails();
    } catch {
      setNsError(t('domains.ns_failed'));
    } finally {
      setSaving(false);
    }
  };

  const handleTogglePrivacy = async () => {
    const next = !privacy;
    setPrivacy(next);
    try {
      await api.togglePrivacy(domain.id, next);
      onShowToast(next ? '✓ WHOIS Privacy включена' : 'WHOIS Privacy отключена');
    } catch {
      setPrivacy(!next);
    }
  };

  const handleToggleLock = async () => {
    const next = !isLocked;
    setIsLocked(next);
    try {
      await api.toggleLock(domain.id, next);
      onShowToast(next ? '✓ Transfer Lock включен' : 'Transfer Lock отключен');
    } catch {
      setIsLocked(!next);
    }
  };

  const handleRenew = async () => {
    setSaving(true);
    try {
      const res = await api.renewDomain(domain.id, telegramId, 1);
      if (res?.payment_url) {
        window.open(res.payment_url, '_blank');
      }
      onShowToast('Заказ на продление создан! Оплатите через СБП.');
    } catch {
      setNsError(t('domains.ns_failed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3 rounded-xl border border-accent/30 bg-card p-3 space-y-3 shadow-xs">
      <div className="no-scrollbar -mx-1 flex gap-1.5 overflow-x-auto border-b border-card-border/60 pb-2 px-1 text-xs">
        {[
          { id: 'presets', label: t('domains.tab_presets') },
          { id: 'dns', label: t('domains.tab_dns') },
          { id: 'ns', label: t('domains.tab_ns') },
          { id: 'security', label: t('domains.tab_security') },
        ].map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              haptic('light');
              setTab(item.id);
            }}
            className={`badge shrink-0 cursor-pointer px-2.5 py-1 font-semibold transition-colors ${
              tab === item.id
                ? 'border-accent bg-accent text-accent-ink'
                : 'border-card-border bg-card text-muted hover:border-accent hover:text-ink'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {loading && (
        <div className="flex items-center justify-center py-4 text-xs text-muted gap-2">
          <Spinner /> {t('common.loading')}
        </div>
      )}

      {!loading && (
        <>
          {tab === 'presets' && (
            <div className="space-y-3">
              <div className="rounded-lg border border-card-border/70 bg-card/70 p-2.5 space-y-2">
                <div>
                  <p className="text-xs font-bold text-ink flex items-center gap-1.5">
                    <span>🚀</span> {t('domains.preset_vps_title')}
                  </p>
                  <p className="text-[11px] text-muted">{t('domains.preset_vps_desc')}</p>
                </div>

                {servers.length > 0 ? (
                  <div className="flex items-center justify-between gap-2 pt-1">
                    <span className="text-xs font-mono font-medium text-ink bg-card-border/20 px-2 py-1 rounded">
                      {servers[0].location_code?.toUpperCase()} · {servers[0].ip_address}
                    </span>
                    <button
                      type="button"
                      disabled={saving}
                      onClick={() => handleApplyPreset('vps', servers[0].ip_address)}
                      className="btn-accent px-3 py-1.5 text-xs font-semibold cursor-pointer"
                    >
                      {t('domains.preset_vps_btn')}
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 pt-1">
                    <input
                      type="text"
                      placeholder="IP: 45.192.12.105"
                      value={vpsIpInput}
                      onChange={(e) => setVpsIpInput(e.target.value)}
                      className="input text-xs flex-1 py-1 px-2 font-mono"
                    />
                    <button
                      type="button"
                      disabled={saving || !vpsIpInput}
                      onClick={() => handleApplyPreset('vps', vpsIpInput)}
                      className="btn-accent px-3 py-1.5 text-xs font-semibold cursor-pointer shrink-0"
                    >
                      {t('domains.preset_vps_btn')}
                    </button>
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-card-border/70 bg-card/70 p-2.5 space-y-2">
                <div>
                  <p className="text-xs font-bold text-ink flex items-center gap-1.5">
                    <span>✈️</span> {t('domains.preset_tg_title')}
                  </p>
                  <p className="text-[11px] text-muted">{t('domains.preset_tg_desc')}</p>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    placeholder={t('domains.preset_tg_placeholder')}
                    value={tgTarget}
                    onChange={(e) => setTgTarget(e.target.value)}
                    className="input text-xs flex-1 py-1 px-2"
                  />
                  <button
                    type="button"
                    disabled={saving || !tgTarget}
                    onClick={() => handleApplyPreset('telegram', tgTarget)}
                    className="btn-accent px-3 py-1.5 text-xs font-semibold cursor-pointer shrink-0"
                  >
                    {t('domains.preset_tg_btn')}
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between rounded-lg border border-card-border/70 bg-card/70 p-2.5">
                <div>
                  <p className="text-xs font-bold text-ink flex items-center gap-1.5">
                    <span>📧</span> {t('domains.preset_mail_title')}
                  </p>
                  <p className="text-[11px] text-muted">{t('domains.preset_mail_desc')}</p>
                </div>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => handleApplyPreset('yandex_mail')}
                  className="btn-ghost border border-card-border hover:border-accent px-3 py-1.5 text-xs font-semibold cursor-pointer shrink-0"
                >
                  {t('domains.preset_mail_btn')}
                </button>
              </div>

              <div className="flex items-center justify-between rounded-lg border border-card-border/70 bg-card/70 p-2.5">
                <div>
                  <p className="text-xs font-bold text-ink flex items-center gap-1.5">
                    <span>☁️</span> {t('domains.preset_cf_title')}
                  </p>
                  <p className="text-[11px] text-muted">{t('domains.preset_cf_desc')}</p>
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

          {tab === 'dns' && (
            <div className="space-y-3">
              <div className="space-y-2">
                {records.map((rec, idx) => (
                  <div key={idx} className="flex items-center gap-1.5 text-xs">
                    <select
                      value={rec.type}
                      onChange={(e) => {
                        const copy = [...records];
                        copy[idx].type = e.target.value;
                        setRecords(copy);
                      }}
                      className="badge bg-card border-card-border py-1 px-1.5 font-bold text-accent"
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
                      className="input py-1 px-1.5 w-16 text-xs font-mono"
                    />

                    <input
                      type="text"
                      placeholder="Значение"
                      value={rec.value}
                      onChange={(e) => {
                        const copy = [...records];
                        copy[idx].value = e.target.value;
                        setRecords(copy);
                      }}
                      className="input py-1 px-1.5 flex-1 text-xs font-mono"
                    />

                    <select
                      value={rec.ttl || 300}
                      onChange={(e) => {
                        const copy = [...records];
                        copy[idx].ttl = Number(e.target.value);
                        setRecords(copy);
                      }}
                      className="badge bg-card border-card-border py-1 px-1 text-[10px]"
                    >
                      <option value={300}>5м</option>
                      <option value={3600}>1ч</option>
                      <option value={86400}>1д</option>
                    </select>

                    <button
                      type="button"
                      onClick={() => setRecords(records.filter((_, i) => i !== idx))}
                      className="text-muted hover:text-red-500 p-1 cursor-pointer"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>

              <div className="flex items-center justify-between pt-1">
                <button
                  type="button"
                  onClick={() =>
                    setRecords([...records, { type: 'A', host: '@', value: '', ttl: 300 }])
                  }
                  className="badge border border-accent/40 bg-card text-accent font-semibold px-2.5 py-1 text-xs cursor-pointer hover:bg-accent/10"
                >
                  {t('domains.dns_add')}
                </button>

                <button
                  type="button"
                  disabled={saving}
                  onClick={handleSaveDns}
                  className="btn-accent px-4 py-1.5 text-xs font-semibold cursor-pointer"
                >
                  {saving ? <Spinner /> : t('domains.dns_save')}
                </button>
              </div>
            </div>
          )}

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

          {tab === 'security' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-lg border border-card-border/70 bg-card/70 p-2.5">
                <div>
                  <p className="text-xs font-bold text-ink">{t('domains.privacy_title')}</p>
                  <p className="text-[11px] text-muted">{t('domains.privacy_desc')}</p>
                </div>
                <button
                  type="button"
                  onClick={handleTogglePrivacy}
                  className={`badge px-3 py-1 text-xs font-bold cursor-pointer transition-colors ${
                    privacy
                      ? 'border-accent bg-accent text-accent-ink'
                      : 'border-card-border bg-card text-muted'
                  }`}
                >
                  {privacy ? 'ВКЛ' : 'ВЫКЛ'}
                </button>
              </div>

              <div className="flex items-center justify-between rounded-lg border border-card-border/70 bg-card/70 p-2.5">
                <div>
                  <p className="text-xs font-bold text-ink">{t('domains.lock_title')}</p>
                  <p className="text-[11px] text-muted">{t('domains.lock_desc')}</p>
                </div>
                <button
                  type="button"
                  onClick={handleToggleLock}
                  className={`badge px-3 py-1 text-xs font-bold cursor-pointer transition-colors ${
                    isLocked
                      ? 'border-accent bg-accent text-accent-ink'
                      : 'border-card-border bg-card text-muted'
                  }`}
                >
                  {isLocked ? 'ЗАБЛОКИРОВАН' : 'ОТКРЫТ'}
                </button>
              </div>

              <div className="rounded-lg border border-card-border/70 bg-card/70 p-2.5 space-y-2">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-bold text-ink">{t('domains.epp_title')}</p>
                    <p className="text-[11px] text-muted">{t('domains.epp_desc')}</p>
                  </div>
                  {!showEpp ? (
                    <button
                      type="button"
                      onClick={() => setShowEpp(true)}
                      className="btn-ghost border border-card-border px-2.5 py-1 text-xs font-medium cursor-pointer"
                    >
                      {t('domains.epp_show')}
                    </button>
                  ) : null}
                </div>

                {showEpp && (
                  <div className="flex items-center justify-between bg-card-border/20 px-2 py-1.5 rounded font-mono text-xs">
                    <span className="font-bold text-accent">{details?.auth_code || 'EPP-DEMO-CODE'}</span>
                    <button
                      type="button"
                      onClick={() => {
                        navigator.clipboard?.writeText(details?.auth_code || '');
                        onShowToast('EPP-код скопирован!');
                      }}
                      className="badge border border-accent/40 bg-card text-accent px-2 py-0.5 text-[11px] cursor-pointer hover:bg-accent/10"
                    >
                      📋 Копировать
                    </button>
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-xs font-bold text-ink">{t('domains.renew_title')}</p>
                    <p className="text-[11px] text-muted">{t('domains.renew_desc')}</p>
                  </div>
                  {details?.renew_price_rub ? (
                    <span className="font-bold text-sm text-ink">
                      {Number(details.renew_price_rub).toLocaleString('ru-RU')} ₽ / год
                    </span>
                  ) : null}
                </div>

                <button
                  type="button"
                  disabled={saving}
                  onClick={handleRenew}
                  className="btn-accent w-full py-2 text-xs font-bold flex items-center justify-center gap-1.5 cursor-pointer shadow-xs"
                >
                  <span>🛒</span>
                  <span>{t('domains.renew_btn')}</span>
                </button>
              </div>
            </div>
          )}

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
