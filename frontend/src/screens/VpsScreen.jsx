/** VPS & VPN 1-click deployment screen.
 *
 * Allows users to choose a location (Netherlands, Poland, Kazakhstan, etc.),
 * choose a configuration preset, and provision a personal VLESS-Reality VPN in 1 click.
 */
import { useCallback, useEffect, useState } from 'react';

import api from '../lib/api';
import { formatDate } from '../lib/i18n';
import StatusBadge from '../components/StatusBadge';
import { EmptyState, ErrorNote, SkeletonList, Spinner, Toast } from '../components/ui';

export default function VpsScreen({ t, telegramId }) {
  const [mode, setMode] = useState('order'); // 'order' | 'my'
  const [locations, setLocations] = useState(null);
  const [presets, setPresets] = useState(null);
  const [myServers, setMyServers] = useState(null);

  const [selectedLoc, setSelectedLoc] = useState('nl');
  const [selectedPreset, setSelectedPreset] = useState('start');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);
  const [qrModalUrl, setQrModalUrl] = useState(null);

  // Load catalogue (locations + presets)
  useEffect(() => {
    Promise.all([api.vpsLocations(), api.vpsPresets()])
      .then(([locs, pres]) => {
        setLocations(Array.isArray(locs) ? locs : []);
        setPresets(Array.isArray(pres) ? pres : []);
        if (locs?.length > 0) setSelectedLoc(locs[0].code);
        if (pres?.length > 0) setSelectedPreset(pres[0].code);
      })
      .catch(() => {
        // Fallback default presets if offline
        setLocations([
          { code: 'nl', name: 'Нидерланды', flag: '🇳🇱', label: 'Амстердам' },
          { code: 'pl', name: 'Польша', flag: '🇵🇱', label: 'Варшава' },
          { code: 'kz', name: 'Казахстан', flag: '🇰🇿', label: 'Алматы' },
          { code: 'fi', name: 'Финляндия', flag: '🇫🇮', label: 'Хельсинки' },
        ]);
        setPresets([
          {
            code: 'start',
            name: 'Старт',
            cpu: 1,
            ram_mb: 1024,
            disk_gb: 20,
            price_rub: 790,
          },
          {
            code: 'standard',
            name: 'Стандарт',
            cpu: 2,
            ram_mb: 2048,
            disk_gb: 40,
            price_rub: 1290,
          },
          {
            code: 'pro',
            name: 'Про',
            cpu: 4,
            ram_mb: 4096,
            disk_gb: 80,
            price_rub: 2490,
          },
        ]);
      });
  }, []);

  // Load user servers
  const loadMyServers = useCallback(async () => {
    if (!telegramId) return;
    try {
      const list = await api.myServers(telegramId);
      setMyServers(Array.isArray(list) ? list : []);
    } catch {
      setMyServers([]);
    }
  }, [telegramId]);

  useEffect(() => {
    if (mode === 'my') {
      loadMyServers();
    }
  }, [mode, loadMyServers]);

  const activePreset = presets?.find((p) => p.code === selectedPreset) || presets?.[0];

  async function handleOrder(e) {
    e.preventDefault();
    if (!telegramId || !activePreset) return;
    setLoading(true);
    setError(null);

    try {
      const resp = await api.createServerOrder(telegramId, {
        location: selectedLoc,
        preset: selectedPreset,
        name: `vpn-${selectedLoc}-${Date.now().toString(36).slice(-4)}`,
      });

      if (resp?.payment_url) {
        window.location.href = resp.payment_url;
      } else {
        setToast(t('vps.key_copied') || 'Сервер создан!');
        setMode('my');
        loadMyServers();
      }
    } catch (err) {
      setError(err?.message || t('common.error'));
    } finally {
      setLoading(false);
    }
  }

  async function copyKey(vlessLink) {
    if (!vlessLink) return;
    try {
      await navigator.clipboard.writeText(vlessLink);
      setToast(t('vps.key_copied'));
    } catch {
      // Fallback prompt for clipboard
      window.prompt('Скопируйте ключ VLESS:', vlessLink);
    }
  }

  async function handleDelete(serverId) {
    if (!window.confirm(t('vps.delete_confirm'))) return;
    try {
      await api.deleteServer(telegramId, serverId);
      loadMyServers();
      setToast('Сервер удален');
    } catch {
      setError('Не удалось удалить сервер.');
    }
  }

  return (
    <div className="space-y-4">
      {/* Sub-navigation */}
      <div className="flex items-center gap-1 border-b border-line pb-2">
        <button
          type="button"
          onClick={() => setMode('order')}
          className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
            mode === 'order'
              ? 'bg-elevated text-ink shadow-sm'
              : 'text-muted hover:text-ink'
          }`}
        >
          {t('vps.tab_new')}
        </button>
        <button
          type="button"
          onClick={() => setMode('my')}
          className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
            mode === 'my'
              ? 'bg-elevated text-ink shadow-sm'
              : 'text-muted hover:text-ink'
          }`}
        >
          <span>{t('vps.tab_my')}</span>
          {myServers?.length ? (
            <span className="rounded-full bg-accent/20 px-1.5 py-0.2 text-[10px] font-semibold text-accent leading-none">
              {myServers.length}
            </span>
          ) : null}
        </button>
      </div>

      {error ? <ErrorNote message={error} onDismiss={() => setError(null)} /> : null}
      {toast ? <Toast message={toast} onDone={() => setToast(null)} /> : null}

      {/* QR Code Modal */}
      {qrModalUrl ? (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
          onClick={() => setQrModalUrl(null)}
        >
          <div
            className="w-full max-w-xs rounded-2xl border border-line bg-surface p-6 text-center shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold text-ink">{t('vps.qr_code')}</h3>
            <p className="mt-1 text-xs text-muted">
              Отсканируйте камерой в приложении Happ, v2rayNG или Streisand
            </p>
            <div className="mt-4 flex justify-center rounded-xl bg-white p-3">
              <img
                src={`https://api.qrserver.com/v1/create-qr-code/?size=220x220&margin=4&data=${encodeURIComponent(
                  qrModalUrl,
                )}`}
                alt="VLESS QR Code"
                className="h-48 w-48"
              />
            </div>
            <button
              type="button"
              onClick={() => setQrModalUrl(null)}
              className="btn-primary mt-4 w-full py-2 text-xs"
            >
              {t('common.close')}
            </button>
          </div>
        </div>
      ) : null}

      {/* Mode: Order */}
      {mode === 'order' ? (
        <form onSubmit={handleOrder} className="space-y-4">
          <div className="card space-y-1.5 p-4">
            <h2 className="text-sm font-semibold text-ink">{t('vps.title')}</h2>
            <p className="text-xs text-muted">{t('vps.subtitle')}</p>
          </div>

          {/* Location picker */}
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted">
              {t('vps.location')}
            </label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {locations?.map((loc) => {
                const active = selectedLoc === loc.code;
                return (
                  <button
                    key={loc.code}
                    type="button"
                    onClick={() => setSelectedLoc(loc.code)}
                    className={`flex flex-col items-start rounded-xl border p-3 text-left transition-all ${
                      active
                        ? 'border-accent bg-accent/10 shadow-sm'
                        : 'border-line bg-surface hover:border-faint'
                    }`}
                  >
                    <span className="text-2xl">{loc.flag || '🌐'}</span>
                    <span className="mt-1.5 text-xs font-semibold text-ink">
                      {loc.name}
                    </span>
                    <span className="text-[11px] text-muted">{loc.label || loc.code}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Preset picker */}
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wider text-muted">
              {t('vps.plan')}
            </label>
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
              {presets?.map((p) => {
                const active = selectedPreset === p.code;
                return (
                  <button
                    key={p.code}
                    type="button"
                    onClick={() => setSelectedPreset(p.code)}
                    className={`flex flex-col justify-between rounded-xl border p-3.5 text-left transition-all ${
                      active
                        ? 'border-accent bg-accent/10 shadow-sm'
                        : 'border-line bg-surface hover:border-faint'
                    }`}
                  >
                    <div>
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-ink">{p.name}</span>
                        <span className="text-xs font-semibold text-accent tabular-nums">
                          {p.price_rub} ₽/мес
                        </span>
                      </div>
                      <div className="mt-2 space-y-0.5 text-[11px] text-muted">
                        <div>
                          • {p.cpu} vCPU · {Math.round(p.ram_mb / 1024)} GB RAM
                        </div>
                        <div>• {p.disk_gb} GB NVMe SSD</div>
                        <div>• Безлимитный трафик 1 Gbps</div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Value banner */}
          <div className="card space-y-2 border-accent/30 bg-surface/80 p-4">
            <div className="flex items-center gap-2 text-xs font-semibold text-ink">
              <span className="text-accent">⚡</span>
              <span>{t('vps.vpn_option')}</span>
            </div>
            <p className="text-[11px] leading-relaxed text-muted">
              {t('vps.vpn_hint')}. Протокол маскируется под трафик Google/Apple и
              стабильно работает в РФ без блокировок.
            </p>
            <p className="text-[10px] text-faint">{t('vps.apps')}</p>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="btn-primary flex w-full items-center justify-center gap-2 py-3 text-sm font-semibold"
          >
            {loading ? <Spinner className="h-4 w-4" /> : null}
            {loading
              ? t('vps.creating')
              : t('vps.buy', { price: activePreset?.price_rub || 790 })}
          </button>
        </form>
      ) : null}

      {/* Mode: My Servers */}
      {mode === 'my' ? (
        <div className="space-y-3">
          {myServers === null ? <SkeletonList rows={3} /> : null}
          {myServers && myServers.length === 0 ? (
            <div className="space-y-4 py-4 text-center">
              <EmptyState icon="⚡" title={t('vps.empty_servers')} />
              <p className="text-xs text-muted">{t('vps.empty_hint')}</p>
              <button
                type="button"
                onClick={() => setMode('order')}
                className="btn-primary mx-auto px-4 py-2 text-xs"
              >
                {t('vps.tab_new')}
              </button>
            </div>
          ) : null}

          {myServers && myServers.length > 0 ? (
            <div className="space-y-3">
              {myServers.map((server) => (
                <div
                  key={server.id}
                  className="card space-y-3 p-4 transition-all hover:border-line/80"
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-lg">
                          {server.location_code === 'nl'
                            ? '🇳🇱'
                            : server.location_code === 'pl'
                              ? '🇵🇱'
                              : server.location_code === 'kz'
                                ? '🇰🇿'
                                : '🌐'}
                        </span>
                        <span className="text-sm font-semibold text-ink">
                          {server.name || server.preset_name}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center gap-3 text-xs text-muted">
                        <span>{server.location_name || server.location_code}</span>
                        {server.ip_address ? (
                          <span className="font-mono text-ink">
                            {server.ip_address}
                          </span>
                        ) : null}
                        {server.expires_at ? (
                          <span>до {formatDate(server.expires_at)}</span>
                        ) : null}
                      </div>
                    </div>

                    <StatusBadge status={server.status} label={server.status} />
                  </div>

                  {/* VLESS Key Block */}
                  {server.vless_link ? (
                    <div className="space-y-2 rounded-xl border border-line/60 bg-elevated/40 p-3">
                      <div className="flex items-center justify-between text-xs font-medium text-ink">
                        <span>Ключ подключения VLESS</span>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setQrModalUrl(server.vless_link)}
                            className="rounded border border-line bg-surface px-2 py-1 text-[11px] font-medium text-muted hover:border-faint hover:text-ink"
                          >
                            QR-код
                          </button>
                          <button
                            type="button"
                            onClick={() => copyKey(server.vless_link)}
                            className="btn-primary px-2.5 py-1 text-[11px] font-medium"
                          >
                            Скопировать
                          </button>
                        </div>
                      </div>
                      <p className="truncate font-mono text-[11px] text-muted">
                        {server.vless_link}
                      </p>
                    </div>
                  ) : null}

                  <div className="flex items-center justify-between pt-1 text-xs">
                    <span className="text-[11px] text-muted">
                      {server.cpu} vCPU · {Math.round(server.ram_mb / 1024)} GB RAM ·{' '}
                      {server.disk_gb} GB
                    </span>
                    <button
                      type="button"
                      onClick={() => handleDelete(server.id)}
                      className="text-muted transition-colors hover:text-danger"
                    >
                      {t('vps.delete')}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
