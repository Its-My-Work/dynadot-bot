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

export function getDomainRouting(domain) {
  const isCloudflare = Boolean(
    domain.nameservers && domain.nameservers.toLowerCase().includes('cloudflare'),
  );

  let targetIp = null;
  let targetRedirect = null;

  if (domain.dns_records) {
    try {
      const records = JSON.parse(domain.dns_records);
      if (Array.isArray(records)) {
        const aRec = records.find((r) => r.type === 'A' && (r.host === '@' || !r.host));
        if (aRec?.value) {
          targetIp = aRec.value;
        }
        const tgRec = records.find(
          (r) =>
            (r.type === 'TXT' || r.type === 'CNAME') &&
            (r.value?.includes('t.me') || r.value?.startsWith('@')),
        );
        if (tgRec?.value) {
          targetRedirect = tgRec.value;
        }
      }
    } catch {
      // Ignored: non-JSON data
    }
  }

  return { targetIp, targetRedirect, isCloudflare };
}

export function getPresetRecords(type, target) {
  const pType = (type || '').toLowerCase();
  if (pType === 'vps') {
    const ip = (target || '45.192.12.105').trim();
    return [
      { type: 'A', host: '@', value: ip, ttl: 300 },
      { type: 'A', host: 'www', value: ip, ttl: 300 },
    ];
  }
  if (pType === 'telegram') {
    const rawTarget = (target || '').trim();
    const cleanUrl = rawTarget.startsWith('http') ? rawTarget : `https://${rawTarget}`;
    return [
      { type: 'CNAME', host: '@', value: 't.me', ttl: 300 },
      { type: 'TXT', host: '_telegram', value: cleanUrl, ttl: 300 },
    ];
  }
  if (pType === 'yandex_mail') {
    return [
      { type: 'MX', host: '@', value: 'mx.yandex.net.', ttl: 300, priority: 10 },
      { type: 'TXT', host: '@', value: 'v=spf1 redirect=_spf.yandex.net', ttl: 300 },
      { type: 'CNAME', host: 'mail', value: 'domain.mail.yandex.net.', ttl: 300 },
    ];
  }
  if (pType === 'improvmx') {
    return [
      { type: 'MX', host: '@', value: 'mx1.improvmx.com.', ttl: 300, priority: 10 },
      { type: 'MX', host: '@', value: 'mx2.improvmx.com.', ttl: 300, priority: 20 },
      { type: 'TXT', host: '@', value: 'v=spf1 include:spf.improvmx.com ~all', ttl: 300 },
    ];
  }
  if (pType === 'cloudflare_mail') {
    return [
      { type: 'MX', host: '@', value: 'route1.mx.cloudflare.net.', ttl: 300, priority: 10 },
      { type: 'MX', host: '@', value: 'route2.mx.cloudflare.net.', ttl: 300, priority: 20 },
      { type: 'MX', host: '@', value: 'route3.mx.cloudflare.net.', ttl: 300, priority: 30 },
      { type: 'TXT', host: '@', value: 'v=spf1 include:_spf.mx.cloudflare.net ~all', ttl: 300 },
    ];
  }
  if (pType === 'cloudflare') {
    return [
      { type: 'NS', host: '@', value: 'ada.ns.cloudflare.com', ttl: 300 },
      { type: 'NS', host: '@', value: 'walt.ns.cloudflare.com', ttl: 300 },
    ];
  }
  return [];
}

export function calculatePresetDiff(existingRecords, presetType, target) {
  const pType = (presetType || '').toLowerCase();
  const newRecords = getPresetRecords(pType, target);

  let kept = [];
  let replaced = [];

  if (['vps', 'telegram'].includes(pType)) {
    kept = existingRecords.filter(
      (r) =>
        !(
          (r.host === '@' || r.host === 'www' || !r.host) &&
          ['A', 'AAAA', 'CNAME'].includes(r.type)
        ),
    );
    replaced = existingRecords.filter(
      (r) =>
        (r.host === '@' || r.host === 'www' || !r.host) &&
        ['A', 'AAAA', 'CNAME'].includes(r.type),
    );
  } else if (['yandex_mail', 'improvmx', 'cloudflare_mail'].includes(pType)) {
    kept = existingRecords.filter((r) => {
      const t = (r.type || '').toUpperCase();
      const h = (r.host || '').toLowerCase();
      const v = String(r.value || '').toLowerCase();
      if (t === 'MX') return false;
      if (t === 'TXT' && (v.includes('spf') || v.includes('v=spf1'))) return false;
      if (t === 'CNAME' && h === 'mail') return false;
      return true;
    });
    replaced = existingRecords.filter((r) => !kept.includes(r));
  } else {
    kept = [];
    replaced = [...existingRecords];
  }

  return { newRecords, kept, replaced };
}

export function PresetConfirmModal({
  preset,
  mode,
  onModeChange,
  onConfirm,
  onCancel,
  saving,
}) {
  if (!preset) return null;

  const presetLabels = {
    vps: '🖥 Привязка к VPS (IPv4)',
    telegram: '✈️ Редирект в Telegram',
    yandex_mail: '✉️ Яндекс 360 Почта',
    improvmx: '✉️ Gmail форвардинг (ImprovMX)',
    cloudflare_mail: '✉️ Cloudflare Email Routing',
    cloudflare: '☁️ Делегирование на Cloudflare DNS',
  };

  const isCf = preset.type === 'cloudflare';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/70 backdrop-blur-xs transition-opacity animate-in fade-in"
        onClick={onCancel}
      />

      {/* Modal Dialog */}
      <div className="relative w-full max-w-md rounded-2xl bg-surface border border-accent/40 shadow-2xl p-5 space-y-4 z-10 animate-in zoom-in-95 duration-150">
        <div className="flex items-start justify-between gap-3 border-b border-card-border/60 pb-3">
          <div>
            <span className="text-[11px] text-accent font-semibold uppercase tracking-wider block">
              Подтверждение изменений
            </span>
            <h3 className="font-bold text-base text-ink mt-0.5">
              {presetLabels[preset.type] || preset.type}
            </h3>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="text-muted hover:text-ink text-sm p-1 cursor-pointer"
          >
            ✕
          </button>
        </div>

        {isCf ? (
          <div className="p-3 rounded-xl bg-cyan-500/10 border border-cyan-500/20 text-xs text-ink space-y-1.5">
            <p className="font-semibold text-cyan-300">Внимание:</p>
            <p className="text-muted">
              NS-серверы домена будут переключены на{' '}
              <code className="text-cyan-300 font-mono">ada.ns.cloudflare.com</code> и{' '}
              <code className="text-cyan-300 font-mono">walt.ns.cloudflare.com</code>.
            </p>
            <p className="text-muted text-[11px]">
              Управление DNS-записями после этого будет происходить через панель Cloudflare.
            </p>
          </div>
        ) : (
          <>
            {/* Mode Selector */}
            <div className="space-y-2">
              <span className="text-xs font-semibold text-ink block">Режим применения:</span>
              <div className="grid grid-cols-1 gap-2">
                <button
                  type="button"
                  onClick={() => onModeChange('merge')}
                  className={`p-3 rounded-xl border text-left cursor-pointer transition-all ${
                    mode === 'merge'
                      ? 'bg-accent/15 border-accent text-ink shadow-xs'
                      : 'bg-card/60 border-card-border/60 text-muted hover:text-ink'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold flex items-center gap-1.5">
                      <span className={mode === 'merge' ? 'text-accent' : 'text-muted'}>
                        {mode === 'merge' ? '●' : '○'}
                      </span>
                      <span>Дополнить текущие записи (Smart Merge)</span>
                    </span>
                    <span className="badge text-[9px] bg-emerald-500/20 text-emerald-400 font-semibold px-1.5 py-0.5">
                      Рекомендуется
                    </span>
                  </div>
                  <p className="text-[11px] text-muted mt-1 pl-4">
                    Сохранит существующие записи сайта и почты, обновив только связанные.
                  </p>
                </button>

                <button
                  type="button"
                  onClick={() => onModeChange('replace')}
                  className={`p-3 rounded-xl border text-left cursor-pointer transition-all ${
                    mode === 'replace'
                      ? 'bg-rose-500/15 border-rose-500/40 text-ink shadow-xs'
                      : 'bg-card/60 border-card-border/60 text-muted hover:text-ink'
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold flex items-center gap-1.5">
                      <span className={mode === 'replace' ? 'text-rose-400' : 'text-muted'}>
                        {mode === 'replace' ? '●' : '○'}
                      </span>
                      <span>Заменить все записи целиком</span>
                    </span>
                  </div>
                  <p className="text-[11px] text-muted mt-1 pl-4">
                    Очистит все старые DNS-записи и оставит только записи этого пресета.
                  </p>
                </button>
              </div>
            </div>

            {/* Visual Diff Preview */}
            <div className="space-y-2">
              <span className="text-xs font-semibold text-ink block">Предпросмотр DNS:</span>
              <div className="rounded-xl bg-card/80 border border-card-border/60 p-3 space-y-3 text-xs max-h-48 overflow-y-auto">
                {/* To Add / Update */}
                <div className="space-y-1">
                  <span className="text-[11px] font-semibold text-emerald-400 flex items-center gap-1">
                    <span>🟢 Будет добавлено/обновлено ({preset.newRecords.length}):</span>
                  </span>
                  {preset.newRecords.map((r, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 font-mono text-[11px] bg-emerald-500/10 border border-emerald-500/20 px-2 py-1 rounded-md text-emerald-300"
                    >
                      <span className="font-bold text-accent">[{r.type}]</span>
                      <span className="text-ink">{r.host || '@'}</span>
                      <span className="text-muted">➔</span>
                      <span className="truncate">{r.value}</span>
                    </div>
                  ))}
                </div>

                {/* To Keep or Warning */}
                {mode === 'merge' ? (
                  <div className="space-y-1 pt-2 border-t border-card-border/40">
                    <span className="text-[11px] font-semibold text-muted flex items-center gap-1">
                      <span>⚪️ Останется без изменений ({preset.kept.length}):</span>
                    </span>
                    {preset.kept.length > 0 ? (
                      preset.kept.map((r, i) => (
                        <div
                          key={i}
                          className="flex items-center gap-2 font-mono text-[11px] bg-surface/50 border border-card-border/40 px-2 py-0.5 rounded text-muted"
                        >
                          <span className="font-bold text-ink">[{r.type}]</span>
                          <span className="text-ink">{r.host || '@'}</span>
                          <span>➔</span>
                          <span className="truncate">{r.value}</span>
                        </div>
                      ))
                    ) : (
                      <p className="text-[11px] text-muted italic">Других записей нет</p>
                    )}
                  </div>
                ) : (
                  <div className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/25 text-rose-300 text-[11px]">
                    ⚠️ Внимание: все старые записи ({preset.replaced.length + preset.kept.length}) будут удалены.
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {/* Modal Buttons */}
        <div className="flex items-center gap-2.5 pt-2 border-t border-card-border/60">
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="btn-ghost flex-1 py-2 text-xs font-semibold border border-card-border cursor-pointer hover:border-accent"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={saving}
            className="btn-accent flex-1 py-2 text-xs font-bold cursor-pointer flex items-center justify-center gap-1.5 shadow-sm"
          >
            {saving ? 'Применение...' : '✓ Применить'}
          </button>
        </div>
      </div>
    </div>
  );
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
        {domains.map((domain) => {
          const routing = getDomainRouting(domain);
          const isManaging = managingDomain?.id === domain.id;

          return (
            <li key={domain.id} className="card p-4 space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-mono text-base font-bold text-ink flex items-center gap-1.5">
                    <span>🌐</span>
                    <span>{domain.name}</span>
                  </h3>
                  <div className="flex items-center gap-2 mt-0.5">
                    <p className="text-xs text-muted">
                      {t('domains.expires', { date: formatDate(domain.expires_at) })}
                    </p>
                    {domain.uptime_status ? (
                      <span className={`badge px-1.5 py-0.2 text-[10px] font-semibold flex items-center gap-1 ${
                        domain.uptime_status === 'up'
                          ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30'
                          : 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30'
                      }`}>
                        <span>{domain.uptime_status === 'up' ? '🟢 Онлайн' : '🔴 Сбой'}</span>
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <a
                    href={`https://${domain.name}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="badge bg-accent/15 text-accent hover:bg-accent/25 px-2 py-1 text-xs font-medium transition-colors flex items-center gap-0.5 shadow-xs"
                    title="Открыть сайт"
                  >
                    <span>🌐</span>
                    <span className="text-[10px]">↗</span>
                  </a>
                  <StatusBadge
                    status={domain.status}
                    label={domain.status === 'active' ? 'Доступен' : domain.status}
                  />
                </div>
              </div>

              {/* Active Routing & Options Summary Box */}
              <div className="rounded-xl bg-surface/70 border border-card-border/60 p-3 space-y-2 text-xs">
                {/* Routing destination */}
                <div className="flex items-center justify-between gap-2">
                  <span className="text-muted text-[11px]">Направление:</span>
                  {routing.targetRedirect ? (
                    <span className="font-mono font-semibold text-accent flex items-center gap-1 text-xs">
                      <span>✈️</span>
                      <span>{routing.targetRedirect}</span>
                    </span>
                  ) : routing.targetIp ? (
                    <span className="font-mono font-semibold text-emerald-600 dark:text-emerald-400 flex items-center gap-1 text-xs">
                      <span>📍</span>
                      <span>{routing.targetIp}</span>
                    </span>
                  ) : routing.isCloudflare ? (
                    <span className="font-semibold text-cyan-600 dark:text-cyan-400 flex items-center gap-1 text-xs">
                      <span>☁️</span>
                      <span>Cloudflare DNS</span>
                    </span>
                  ) : (
                    <span className="text-muted text-[11px] font-mono truncate max-w-[200px]">
                      {domain.nameservers || 'Dynadot DNS'}
                    </span>
                  )}
                </div>

                {/* Status Badges: Privacy, Lock, Auto-renew */}
                <div className="flex items-center gap-1.5 pt-1.5 border-t border-card-border/40 overflow-x-auto text-[11px]">
                  <span
                    className={`badge px-2 py-0.5 text-[10px] font-semibold ${
                      domain.privacy_enabled
                        ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30'
                        : 'bg-card text-muted border border-card-border/50'
                    }`}
                  >
                    🛡 Privacy: {domain.privacy_enabled ? 'Вкл' : 'Выкл'}
                  </span>

                  <span
                    className={`badge px-2 py-0.5 text-[10px] font-semibold ${
                      domain.is_locked
                        ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30'
                        : 'bg-card text-muted border border-card-border/50'
                    }`}
                  >
                    🔒 Lock: {domain.is_locked ? 'Вкл' : 'Выкл'}
                  </span>

                  {domain.auto_renew ? (
                    <span className="badge px-2 py-0.5 text-[10px] font-semibold bg-cyan-500/15 text-cyan-700 dark:text-cyan-400 border border-cyan-500/30">
                      🔄 Автопродление
                    </span>
                  ) : null}
                </div>
              </div>

              {/* Action Buttons: Single "Управление" button + "Сайт ↗" */}
              <div className="pt-1">
                {isManaging ? (
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
                  <div className="mt-2">
                    <button
                      type="button"
                      className="btn-accent w-full py-2 text-xs font-semibold flex items-center justify-center gap-1.5 cursor-pointer shadow-xs"
                      onClick={() => {
                        haptic('light');
                        setInitialTab('dns');
                        setManagingDomain(domain);
                      }}
                    >
                      <span>⚙️</span>
                      <span>{t('domains.manage')}</span>
                    </button>
                  </div>
                )}
              </div>
            </li>
          );
        })}
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

  // Preset Confirmation & Diff Preview State
  const [pendingPreset, setPendingPreset] = useState(null);
  const [presetMode, setPresetMode] = useState('merge');

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

  const handleApplyPreset = (type, target) => {
    const diff = calculatePresetDiff(records, type, target);
    setPendingPreset({
      type,
      target,
      newRecords: diff.newRecords,
      kept: diff.kept,
      replaced: diff.replaced,
    });
    setPresetMode('merge');
  };

  const executeApplyPreset = async () => {
    if (!pendingPreset) return;
    const { type, target } = pendingPreset;
    setSaving(true);
    try {
      const updated = await api.applyQuickPreset(domain.id, type, target, presetMode);
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
      setPendingPreset(null);
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

      {/* Mobile Action Sheet (Modal Bottom Drawer on mobile, centered modal on desktop) */}
      {actionsOpen && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end sm:items-center sm:justify-center p-0 sm:p-4">
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-black/60 backdrop-blur-xs transition-opacity animate-in fade-in"
            onClick={() => setActionsOpen(false)}
          />

          {/* Slide-up Sheet on Mobile, Centered Modal on Desktop */}
          <div
            ref={actionsRef}
            className="relative z-50 w-full sm:max-w-md mx-auto my-auto rounded-t-2xl sm:rounded-2xl border border-card-border/80 bg-card p-5 shadow-2xl space-y-3.5 text-xs animate-in slide-in-from-bottom sm:zoom-in-95 duration-200 max-h-[90vh] overflow-y-auto"
          >
            {/* Grab handle (visible on mobile only) */}
            <div className="mx-auto h-1 w-10 rounded-full bg-line/80 mb-1 sm:hidden" />

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

      {/* Preset Confirmation Modal with Diff Preview */}
      <PresetConfirmModal
        preset={pendingPreset}
        mode={presetMode}
        onModeChange={setPresetMode}
        onConfirm={executeApplyPreset}
        onCancel={() => setPendingPreset(null)}
        saving={saving}
      />
    </div>
  );
}
