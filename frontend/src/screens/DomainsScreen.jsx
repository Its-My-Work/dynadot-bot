/** "My domains" + mobile-first domain management: DNS editor, 1-click presets dropdown,
 * nameservers, Action Sheet for WHOIS privacy, transfer lock, EPP code, and renewal.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import api from '../lib/api';
import { formatDate } from '../lib/i18n';
import { haptic } from '../lib/telegram';
import { EmptyState, ErrorNote, SkeletonList, Spinner, Toast } from '../components/ui';
import { useIsDesktop } from '../hooks/useMediaQuery';

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
          className="fixed inset-0 bg-black/80 backdrop-blur-sm transition-opacity animate-in fade-in duration-200"
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
          <div className="p-3 rounded-xl bg-accent/10 border border-accent/20 text-xs text-ink space-y-1.5">
            <p className="font-semibold text-accent">Внимание:</p>
            <p className="text-muted">
              Убедитесь, что ваши NS-серверы в Dynadot заменены на Cloudflare:{' '}
              <code className="text-accent font-mono">ada.ns.cloudflare.com</code> и{' '}
              <code className="text-accent font-mono">walt.ns.cloudflare.com</code>.
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
                    <span className="badge text-[9px] bg-teal-500/15 text-teal-700 dark:text-teal-300 font-semibold px-1.5 py-0.5">
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
                  <span className="text-[11px] font-semibold text-teal-700 dark:text-teal-300 flex items-center gap-1">
                    <span>🟢 Будет добавлено/обновлено ({preset.newRecords.length}):</span>
                  </span>
                  {preset.newRecords.map((r, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 font-mono text-[11px] bg-teal-500/10 border border-teal-500/20 px-2 py-1 rounded-md text-teal-800 dark:text-teal-200"
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

function DomainToggles({
  domain,
  togglingMap,
  activeTooltip,
  setActiveTooltip,
  onTogglePrivacy,
  onToggleLock,
  onToggleAutoRenew,
  compact = false,
}) {
  return (
    <div className={`flex flex-wrap items-center ${compact ? 'gap-1' : 'gap-1.5'} overflow-x-visible text-[11px]`}>
      {/* Privacy Toggle */}
      <div className="relative inline-flex items-center">
        <button
          type="button"
          onClick={() => onTogglePrivacy(domain)}
          disabled={togglingMap[`${domain.id}_privacy`]}
          className={`inline-flex items-center gap-1 ${compact ? 'px-2 py-0.5 rounded-md text-[10px]' : 'px-2.5 py-1 rounded-lg text-[10px]'} font-medium border transition-all cursor-pointer ${
            domain.privacy_enabled
              ? 'bg-accent/10 border-accent/40 text-accent hover:bg-accent/20'
              : 'bg-surface border-line text-muted hover:text-ink'
          }`}
          title="Нажмите для переключения Privacy"
        >
          <span>🛡</span>
          <span>Privacy:</span>
          <span className={domain.privacy_enabled ? 'font-semibold text-accent' : 'font-medium text-muted'}>
            {togglingMap[`${domain.id}_privacy`] ? '...' : domain.privacy_enabled ? 'Вкл' : 'Выкл'}
          </span>
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              setActiveTooltip(activeTooltip === `privacy_${domain.id}` ? null : `privacy_${domain.id}`);
            }}
            className="ml-0.5 text-[10px] opacity-60 hover:opacity-100 hover:text-accent rounded transition-opacity"
            title="Подробнее о Privacy"
          >
            ⓘ
          </span>
        </button>
        {activeTooltip === `privacy_${domain.id}` && (
          <div
            onClick={(e) => e.stopPropagation()}
            className="absolute bottom-full left-0 mb-2 z-40 w-56 p-2.5 rounded-xl bg-surface border border-line shadow-2xl text-[11px] text-ink animate-in fade-in zoom-in-95 duration-150"
          >
            <div className="flex items-center justify-between pb-1 font-semibold text-accent border-b border-line mb-1">
              <span>🛡 WHOIS Privacy</span>
              <button onClick={() => setActiveTooltip(null)} className="text-muted hover:text-ink text-xs">✕</button>
            </div>
            <p className="text-muted leading-tight text-[10px]">
              Скрывает паспортные данные, телефон и email владельца из общедоступной базы WHOIS.
            </p>
          </div>
        )}
      </div>

      {/* Lock Toggle */}
      <div className="relative inline-flex items-center">
        <button
          type="button"
          onClick={() => onToggleLock(domain)}
          disabled={togglingMap[`${domain.id}_lock`]}
          className={`inline-flex items-center gap-1 ${compact ? 'px-2 py-0.5 rounded-md text-[10px]' : 'px-2.5 py-1 rounded-lg text-[10px]'} font-medium border transition-all cursor-pointer ${
            domain.is_locked
              ? 'bg-accent/10 border-accent/40 text-accent hover:bg-accent/20'
              : 'bg-surface border-line text-muted hover:text-ink'
          }`}
          title="Нажмите для переключения Transfer Lock"
        >
          <span>🔒</span>
          <span>Lock:</span>
          <span className={domain.is_locked ? 'font-semibold text-accent' : 'font-medium text-muted'}>
            {togglingMap[`${domain.id}_lock`] ? '...' : domain.is_locked ? 'Вкл' : 'Выкл'}
          </span>
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              setActiveTooltip(activeTooltip === `lock_${domain.id}` ? null : `lock_${domain.id}`);
            }}
            className="ml-0.5 text-[10px] opacity-60 hover:opacity-100 hover:text-accent rounded transition-opacity"
            title="Подробнее о Lock"
          >
            ⓘ
          </span>
        </button>
        {activeTooltip === `lock_${domain.id}` && (
          <div
            onClick={(e) => e.stopPropagation()}
            className="absolute bottom-full left-0 mb-2 z-40 w-56 p-2.5 rounded-xl bg-surface border border-line shadow-2xl text-[11px] text-ink animate-in fade-in zoom-in-95 duration-150"
          >
            <div className="flex items-center justify-between pb-1 font-semibold text-accent border-b border-line mb-1">
              <span>🔒 Transfer Lock</span>
              <button onClick={() => setActiveTooltip(null)} className="text-muted hover:text-ink text-xs">✕</button>
            </div>
            <p className="text-muted leading-tight text-[10px]">
              Блокирует перенос (трансфер) домена к другому регистратору без вашего ведома.
            </p>
          </div>
        )}
      </div>

      {/* Auto-renew Toggle */}
      <div className="relative inline-flex items-center">
        <button
          type="button"
          onClick={() => onToggleAutoRenew(domain)}
          disabled={togglingMap[`${domain.id}_autorenew`]}
          className={`inline-flex items-center gap-1 ${compact ? 'px-2 py-0.5 rounded-md text-[10px]' : 'px-2.5 py-1 rounded-lg text-[10px]'} font-medium border transition-all cursor-pointer ${
            domain.auto_renew
              ? 'bg-accent/10 border-accent/40 text-accent hover:bg-accent/20'
              : 'bg-surface border-line text-muted hover:text-ink'
          }`}
          title="Нажмите для переключения автопродления"
        >
          <span>🔄</span>
          <span>Продление:</span>
          <span className={domain.auto_renew ? 'font-semibold text-accent' : 'font-medium text-muted'}>
            {togglingMap[`${domain.id}_autorenew`] ? '...' : domain.auto_renew ? 'Вкл' : 'Выкл'}
          </span>
          <span
            role="button"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              setActiveTooltip(activeTooltip === `autorenew_${domain.id}` ? null : `autorenew_${domain.id}`);
            }}
            className="ml-0.5 text-[10px] opacity-60 hover:opacity-100 hover:text-accent rounded transition-opacity"
            title="Подробнее об автопродлении"
          >
            ⓘ
          </span>
        </button>
        {activeTooltip === `autorenew_${domain.id}` && (
          <div
            onClick={(e) => e.stopPropagation()}
            className="absolute bottom-full right-0 sm:left-0 mb-2 z-40 w-56 p-2.5 rounded-xl bg-surface border border-line shadow-2xl text-[11px] text-ink animate-in fade-in zoom-in-95 duration-150"
          >
            <div className="flex items-center justify-between pb-1 font-semibold text-accent border-b border-line mb-1">
              <span>🔄 Автопродление</span>
              <button onClick={() => setActiveTooltip(null)} className="text-muted hover:text-ink text-xs">✕</button>
            </div>
            <p className="text-muted leading-tight text-[10px]">
              Автоматически продлевает домен за 30 дней до окончания срока при наличии баланса.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export default function DomainsScreen({ t, telegramId }) {
  const isDesktop = useIsDesktop();
  const [domains, setDomains] = useState(null);
  const [error, setError] = useState(null);
  const [managingDomain, setManagingDomain] = useState(null);
  const [initialTab, setInitialTab] = useState('dns');
  const [toast, setToast] = useState(null);
  const [togglingMap, setTogglingMap] = useState({});
  const [activeTooltip, setActiveTooltip] = useState(null);

  // Close tooltip on outside click
  useEffect(() => {
    if (!activeTooltip) return;
    const handler = () => setActiveTooltip(null);
    window.addEventListener('click', handler);
    return () => window.removeEventListener('click', handler);
  }, [activeTooltip]);

  const handleToggleDomainPrivacy = async (domain) => {
    const key = `${domain.id}_privacy`;
    if (togglingMap[key]) return;
    setTogglingMap((p) => ({ ...p, [key]: true }));
    const nextVal = !domain.privacy_enabled;
    try {
      await api.togglePrivacy(domain.id, nextVal);
      setDomains((prev) =>
        prev.map((d) => (d.id === domain.id ? { ...d, privacy_enabled: nextVal } : d)),
      );
      if (managingDomain?.id === domain.id) {
        setManagingDomain((prev) => ({ ...prev, privacy_enabled: nextVal }));
      }
      setToast({
        message: nextVal ? '✓ WHOIS Privacy включен' : '✓ WHOIS Privacy выключен',
        tone: 'ok',
      });
      haptic('light');
    } catch (err) {
      setToast({ message: err.message || 'Ошибка изменения Privacy', tone: 'danger' });
      haptic('warning');
    } finally {
      setTogglingMap((p) => ({ ...p, [key]: false }));
    }
  };

  const handleToggleDomainLock = async (domain) => {
    const key = `${domain.id}_lock`;
    if (togglingMap[key]) return;
    setTogglingMap((p) => ({ ...p, [key]: true }));
    const nextVal = !domain.is_locked;
    try {
      await api.toggleLock(domain.id, nextVal);
      setDomains((prev) =>
        prev.map((d) => (d.id === domain.id ? { ...d, is_locked: nextVal } : d)),
      );
      if (managingDomain?.id === domain.id) {
        setManagingDomain((prev) => ({ ...prev, is_locked: nextVal }));
      }
      setToast({
        message: nextVal ? '✓ Защита от трансфера включена' : '✓ Защита от трансфера выключена',
        tone: 'ok',
      });
      haptic('light');
    } catch (err) {
      setToast({ message: err.message || 'Ошибка изменения Lock', tone: 'danger' });
      haptic('warning');
    } finally {
      setTogglingMap((p) => ({ ...p, [key]: false }));
    }
  };

  const handleToggleDomainAutoRenew = async (domain) => {
    const key = `${domain.id}_autorenew`;
    if (togglingMap[key]) return;
    setTogglingMap((p) => ({ ...p, [key]: true }));
    const nextVal = !domain.auto_renew;
    try {
      await api.toggleAutoRenew(domain.id, nextVal);
      setDomains((prev) =>
        prev.map((d) => (d.id === domain.id ? { ...d, auto_renew: nextVal } : d)),
      );
      if (managingDomain?.id === domain.id) {
        setManagingDomain((prev) => ({ ...prev, auto_renew: nextVal }));
      }
      setToast({
        message: nextVal ? '✓ Автопродление включено' : '✓ Автопродление выключено',
        tone: 'ok',
      });
      haptic('light');
    } catch (err) {
      setToast({ message: err.message || 'Ошибка изменения автопродления', tone: 'danger' });
      haptic('warning');
    } finally {
      setTogglingMap((p) => ({ ...p, [key]: false }));
    }
  };

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

  // Calculate summary metrics
  const privacyCount = domains.filter((d) => d.privacy_enabled).length;
  const privacyPercent = domains.length ? Math.round((privacyCount / domains.length) * 100) : 100;
  const sortedByExpiry = [...domains].sort((a, b) => new Date(a.expires_at || 0) - new Date(b.expires_at || 0));
  const earliestExpiry = sortedByExpiry[0];

  return (
    <>
      {isDesktop ? (
        <>
          {/* 3 COMPACT KPI STAT CARDS */}
          <div className="grid grid-cols-3 gap-3 mb-5">
            <div className="card rounded-xl p-3.5 flex items-center justify-between bg-surface border border-line shadow-xs">
              <div>
                <div className="text-[10px] font-medium text-muted uppercase tracking-wider">Активные домены</div>
                <div className="text-lg font-semibold text-ink mt-0.5">
                  {domains.length} <span className="text-xs font-normal text-ok ml-1">● все в сети</span>
                </div>
              </div>
              <div className="h-9 w-9 rounded-lg bg-accent/10 text-accent flex items-center justify-center text-base">🌐</div>
            </div>

            <div className="card rounded-xl p-3.5 flex items-center justify-between bg-surface border border-line shadow-xs">
              <div>
                <div className="text-[10px] font-medium text-muted uppercase tracking-wider">Защита WHOIS</div>
                <div className="text-lg font-semibold text-ink mt-0.5">
                  {privacyPercent}% <span className="text-xs font-normal text-muted ml-1">Privacy</span>
                </div>
              </div>
              <div className="h-9 w-9 rounded-lg bg-ok/10 text-ok flex items-center justify-center text-base">🛡</div>
            </div>

            <div className="card rounded-xl p-3.5 flex items-center justify-between bg-surface border border-line shadow-xs">
              <div>
                <div className="text-[10px] font-medium text-muted uppercase tracking-wider">Ближайшее продление</div>
                <div className="text-lg font-semibold text-ink mt-0.5 truncate max-w-[180px]">
                  {earliestExpiry ? formatDate(earliestExpiry.expires_at) : '—'}
                </div>
              </div>
              <div className="h-9 w-9 rounded-lg bg-warn/10 text-warn flex items-center justify-center text-base">📅</div>
            </div>
          </div>

          {/* COMPACT DESKTOP REGISTRY TABLE (38px rows) */}
          <div className="card rounded-2xl overflow-hidden border border-line bg-surface shadow-xs mb-6">
            <div className="px-5 py-2.5 border-b border-line flex items-center justify-between bg-elevated/40">
              <div className="text-xs font-semibold text-ink">Реестр доменов и параметры защит</div>
              <div className="text-[11px] text-muted">Быстрое переключение защит и настройки DNS</div>
            </div>

            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-line text-[10px] font-semibold text-muted uppercase tracking-wider bg-surface/50">
                  <th className="py-2 px-4">Домен</th>
                  <th className="py-2 px-3">Статус</th>
                  <th className="py-2 px-3">Куда ведет</th>
                  <th className="py-2 px-3">Параметры защит</th>
                  <th className="py-2 px-3">Срок действия</th>
                  <th className="py-2 px-4 text-right">Действия</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/60">
                {domains.map((domain) => {
                  const routing = getDomainRouting(domain);
                  return (
                    <tr key={domain.id} className="hover:bg-accent/[0.04] transition-colors group bg-surface">
                      <td className="py-2 px-4">
                        <div className="flex items-center gap-1.5">
                          <span className="text-muted/70 text-xs">🌐</span>
                          <span className="font-mono font-medium text-xs text-ink">{domain.name}</span>
                          <a
                            href={`https://${domain.name}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-muted hover:text-accent text-[10px] p-0.5 opacity-60 group-hover:opacity-100 transition-opacity"
                            title="Открыть сайт"
                          >
                            ↗
                          </a>
                        </div>
                      </td>
                      <td className="py-2 px-3">
                        <span className="inline-flex items-center gap-1.5 text-xs text-ink font-normal">
                          <span
                            className={`h-2 w-2 rounded-full shrink-0 ${
                              domain.uptime_status === 'up'
                                ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]'
                                : 'bg-muted-foreground/40'
                            }`}
                          />
                          <span>{domain.uptime_status === 'up' ? 'Онлайн' : 'Офлайн'}</span>
                        </span>
                      </td>
                      <td className="py-2 px-3">
                        {routing.targetRedirect ? (
                          <span className="font-mono font-normal text-accent inline-flex items-center gap-1 text-[11px] bg-accent/10 px-1.5 py-0.5 rounded border border-accent/20">
                            <span>✈️</span>
                            <span>{routing.targetRedirect}</span>
                          </span>
                        ) : routing.targetIp ? (
                          <span className="font-mono text-xs text-ink/90 inline-flex items-center gap-1">
                            <span className="text-[10px] text-muted">📍</span>
                            <span>{routing.targetIp}</span>
                          </span>
                        ) : routing.isCloudflare ? (
                          <span className="text-xs text-ink font-medium inline-flex items-center gap-1">
                            <span>☁️</span>
                            <span>Cloudflare DNS</span>
                          </span>
                        ) : (
                          <span className="text-muted text-[11px] font-mono truncate max-w-[140px] inline-block">
                            {domain.nameservers || 'Dynadot DNS'}
                          </span>
                        )}
                      </td>
                      <td className="py-2 px-3">
                        <DomainToggles
                          domain={domain}
                          togglingMap={togglingMap}
                          activeTooltip={activeTooltip}
                          setActiveTooltip={setActiveTooltip}
                          onTogglePrivacy={handleToggleDomainPrivacy}
                          onToggleLock={handleToggleDomainLock}
                          onToggleAutoRenew={handleToggleDomainAutoRenew}
                          compact={true}
                        />
                      </td>
                      <td className="py-2 px-3">
                        <div className="text-xs text-muted font-normal whitespace-nowrap">
                          {formatDate(domain.expires_at)}
                        </div>
                      </td>
                      <td className="py-2 px-4 text-right">
                        <button
                          type="button"
                          onClick={() => {
                            haptic('light');
                            setInitialTab('dns');
                            setManagingDomain(domain);
                          }}
                          className="px-2.5 py-1 rounded-lg bg-elevated/70 hover:bg-accent hover:text-accent-ink text-ink text-xs font-medium transition-all cursor-pointer shadow-xs border border-line hover:border-accent inline-flex items-center gap-1"
                        >
                          <span>DNS</span>
                          <span>⚙️</span>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          {/* Mobile Summary Banner (hidden on lg) */}
          <div className="card p-3.5 bg-gradient-to-r from-accent/10 via-surface to-surface border border-accent/20 flex items-center justify-between mb-4">
            <div className="space-y-0.5">
              <div className="text-[11px] font-medium text-accent uppercase tracking-wider">Ваши домены</div>
              <div className="text-sm font-semibold text-ink">{domains.length} {domains.length === 1 ? 'активный домен' : 'активных доменов'}</div>
            </div>
            <div className="h-9 w-9 rounded-xl bg-accent/15 border border-accent/25 flex items-center justify-center text-accent text-base">
              🌐
            </div>
          </div>

          <ul className="space-y-3">
            {domains.map((domain) => {
              const routing = getDomainRouting(domain);

              return (
                <li key={domain.id} className="card p-4 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3 className="font-mono text-[15px] font-medium tracking-tight text-ink flex items-center gap-1.5">
                        <span>🌐</span>
                        <span>{domain.name}</span>
                      </h3>
                      <div className="flex items-center gap-2 mt-0.5">
                        <p className="text-xs text-muted">
                          {t('domains.expires', { date: formatDate(domain.expires_at) })}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <a
                        href={`https://${domain.name}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="badge bg-accent/10 text-accent hover:bg-accent/20 px-2 py-1 text-xs font-medium transition-colors flex items-center gap-0.5"
                        title="Открыть сайт"
                      >
                        <span>🌐</span>
                        <span className="text-[10px]">↗</span>
                      </a>
                      <span className="badge bg-surface text-ink border border-line px-2 py-0.5 text-[11px] font-medium flex items-center gap-1.5 shadow-xs">
                        <span>{domain.uptime_status === 'up' ? '🟢' : '⚪'}</span>
                        <span>{domain.uptime_status === 'up' ? 'Активен · Онлайн' : 'Активен'}</span>
                      </span>
                    </div>
                  </div>

                  {/* Active Routing & Options Summary Box */}
                  <div className="rounded-xl bg-surface/70 border border-line p-3 space-y-2 text-xs">
                    {/* Routing destination */}
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-muted text-[11px]">Куда ведет:</span>
                      {routing.targetRedirect ? (
                        <span className="font-mono font-normal text-accent flex items-center gap-1 text-xs">
                          <span>✈️</span>
                          <span>{routing.targetRedirect}</span>
                        </span>
                      ) : routing.targetIp ? (
                        <span className="font-mono font-normal text-ink flex items-center gap-1 text-xs">
                          <span>📍</span>
                          <span>{routing.targetIp}</span>
                        </span>
                      ) : routing.isCloudflare ? (
                        <span className="font-medium text-ink flex items-center gap-1 text-xs">
                          <span>☁️</span>
                          <span>Cloudflare DNS</span>
                        </span>
                      ) : (
                        <span className="text-muted text-[11px] font-mono truncate max-w-[200px]">
                          {domain.nameservers || 'Dynadot DNS'}
                        </span>
                      )}
                    </div>

                    {/* Interactive Toggles & Management in the same row */}
                    <div className="pt-2 border-t border-line overflow-x-visible flex flex-wrap items-center justify-between gap-2">
                      <DomainToggles
                        domain={domain}
                        togglingMap={togglingMap}
                        activeTooltip={activeTooltip}
                        setActiveTooltip={setActiveTooltip}
                        onTogglePrivacy={handleToggleDomainPrivacy}
                        onToggleLock={handleToggleDomainLock}
                        onToggleAutoRenew={handleToggleDomainAutoRenew}
                      />
                      <button
                        type="button"
                        className="btn-accent px-3 py-1 text-[11px] font-medium flex items-center gap-1.5 cursor-pointer shadow-xs ml-auto shrink-0"
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
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
      {managingDomain && (
        <DomainManager
          t={t}
          domain={managingDomain}
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
      )}
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
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4 bg-black/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="fixed inset-0" onClick={onClose} aria-hidden="true" />
      <div className="relative z-10 w-full sm:max-w-2xl max-h-[92vh] sm:max-h-[85vh] bg-surface rounded-t-3xl sm:rounded-2xl border border-line shadow-2xl flex flex-col animate-in slide-in-from-bottom sm:zoom-in-95 duration-200 overflow-hidden">
        {/* Mobile Swipe Handle */}
        <div className="sm:hidden flex justify-center pt-2.5 pb-1 bg-surface shrink-0">
          <div className="w-10 h-1 bg-line rounded-full" />
        </div>

        {/* Modal Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-line bg-surface/95 shrink-0">
          <div className="flex items-center gap-2.5">
            <span className="font-mono font-medium text-sm text-ink">{domain.name}</span>
            <span className="badge text-[10px] bg-emerald-500/10 text-emerald-800 dark:text-emerald-300 border border-emerald-500/25 font-medium px-2 py-0.5">
              {domain.uptime_status === 'up' ? '🟢 Онлайн' : 'Активен'}
            </span>
          </div>

          <div className="flex items-center gap-2">
            {/* Quick Actions Dropdown Button */}
            <button
              type="button"
              onClick={() => setActionsOpen(true)}
              className="badge border border-accent/40 bg-accent/10 text-accent font-medium px-2.5 py-1 text-xs cursor-pointer hover:bg-accent/20 flex items-center gap-1.5 transition-colors"
            >
              <span>⚡ Действия</span>
              <span className="text-[10px]">▾</span>
            </button>

            {/* Close Modal */}
            <button
              type="button"
              className="text-sm text-muted hover:text-ink cursor-pointer p-1.5 rounded-lg hover:bg-surface/80 transition-colors"
              onClick={onClose}
              aria-label="Закрыть"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Scrollable Modal Content */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-4">

      {/* Mobile Action Sheet (Modal Bottom Drawer on mobile, centered modal on desktop) */}
      {actionsOpen && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end sm:items-center sm:justify-center p-0 sm:p-4">
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-black/80 backdrop-blur-sm transition-opacity animate-in fade-in duration-200"
            onClick={() => setActionsOpen(false)}
          />

          {/* Slide-up Sheet on Mobile, Centered Modal on Desktop */}
          <div
            ref={actionsRef}
            className="relative z-50 w-full sm:max-w-md mx-auto my-auto rounded-t-3xl sm:rounded-2xl border border-card-border/80 bg-surface p-6 shadow-2xl space-y-4 text-xs animate-in slide-in-from-bottom sm:zoom-in-95 duration-200 max-h-[90vh] overflow-y-auto"
          >
            {/* Grab handle (visible on mobile only) */}
            <div className="mx-auto h-1.5 w-12 rounded-full bg-line/80 mb-2 sm:hidden" />

            <div className="flex items-center justify-between pb-3 border-b border-card-border/60">
              <div className="flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-accent/20 text-accent font-bold">
                  ⚡
                </span>
                <div>
                  <h3 className="font-bold text-sm text-ink">Управление доменом</h3>
                  <p className="text-[11px] font-mono text-muted">{domain.name}</p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setActionsOpen(false)}
                className="text-muted hover:text-ink text-sm p-1.5 rounded-lg hover:bg-card cursor-pointer transition-colors"
              >
                ✕
              </button>
            </div>

            <div className="space-y-2.5 pt-1">
              {/* Copy EPP Code */}
              <button
                type="button"
                onClick={() => {
                  handleCopyEpp();
                  setActionsOpen(false);
                }}
                className="w-full flex items-center justify-between p-3.5 rounded-xl bg-card hover:bg-card/80 border border-card-border/60 text-left text-ink cursor-pointer transition-all hover:border-accent/40 shadow-xs"
              >
                <div className="flex items-center gap-3">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface text-base">📋</span>
                  <div>
                    <div className="font-semibold text-ink">Скопировать EPP-код</div>
                    <div className="text-[11px] text-muted">Код авторизации для переноса к другому регистратору</div>
                  </div>
                </div>
                <span className="badge border border-accent/40 bg-accent/15 text-accent font-mono text-[11px] px-2.5 py-1 shrink-0 font-bold">
                  auth-code
                </span>
              </button>

              {/* Toggle Privacy */}
              <button
                type="button"
                onClick={handleTogglePrivacy}
                className="w-full flex items-center justify-between p-3.5 rounded-xl bg-card hover:bg-card/80 border border-card-border/60 text-left text-ink cursor-pointer transition-all hover:border-accent/40 shadow-xs"
              >
                <div className="flex items-center gap-3">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface text-base">🛡</span>
                  <div>
                    <div className="font-semibold text-ink">WHOIS Privacy</div>
                    <div className="text-[11px] text-muted">Скрытие личных контактов в публичном WHOIS</div>
                  </div>
                </div>
                <span className={`badge text-[11px] px-2.5 py-1 font-bold shrink-0 ${privacy ? 'bg-card text-ink border border-card-border/80' : 'bg-surface text-muted border border-card-border'}`}>
                  {privacy ? '✓ ВКЛ' : 'ВЫКЛ'}
                </span>
              </button>

              {/* Toggle Lock */}
              <button
                type="button"
                onClick={handleToggleLock}
                className="w-full flex items-center justify-between p-3.5 rounded-xl bg-card hover:bg-card/80 border border-card-border/60 text-left text-ink cursor-pointer transition-all hover:border-accent/40 shadow-xs"
              >
                <div className="flex items-center gap-3">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-surface text-base">🔒</span>
                  <div>
                    <div className="font-semibold text-ink">Защита от трансфера (Lock)</div>
                    <div className="text-[11px] text-muted">Блокировка несанкционированной смены регистратора</div>
                  </div>
                </div>
                <span className={`badge text-[11px] px-2.5 py-1 font-bold shrink-0 ${lock ? 'bg-card text-ink border border-card-border/80' : 'bg-surface text-muted border border-card-border'}`}>
                  {lock ? '✓ ВКЛ' : 'ВЫКЛ'}
                </span>
              </button>

              {/* Renew */}
              <button
                type="button"
                onClick={() => {
                  handleRenew();
                  setActionsOpen(false);
                }}
                className="w-full flex items-center justify-between p-3.5 rounded-xl bg-accent/15 hover:bg-accent/25 border border-accent/40 text-left text-ink cursor-pointer transition-all shadow-md mt-3"
              >
                <div className="flex items-center gap-3">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/20 text-base">🛒</span>
                  <div>
                    <div className="font-bold text-accent">Продлить домен на 1 год</div>
                    <div className="text-[11px] text-muted">Мгновенное продление срока регистрации</div>
                  </div>
                </div>
                <span className="font-bold text-sm text-ink shrink-0 bg-surface/80 px-2.5 py-1 rounded-lg border border-accent/30 font-mono">
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
          <div className="flex justify-end pt-2 border-t border-line">
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
      </div>
    </div>
  );
}
