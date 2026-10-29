/** Search screen: the product's front door.
 *
 * Design constraints that drove this:
 *
 * * **One input, no form chrome.** The fastest path from "I have a name idea"
 *   to "I can buy it" is a single field that searches every zone at once.
 * * **Debounced + abortable.** Each search is a serialised registrar call, so
 *   firing one per keystroke would queue garbage behind the user's real query.
 * * **Taken results stay visible but dimmed** rather than being filtered out:
 *   seeing `.com` taken is information, and silently dropping rows makes the
 *   zone grid look random.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import api, { ApiError } from '../lib/api';
import { formatPrice } from '../lib/i18n';
import { haptic } from '../lib/telegram';
import { EmptyState, ErrorNote, SkeletonList, Spinner } from '../components/ui';

const DEBOUNCE_MS = 450;

/** Strip anything that cannot be in a domain label. */
export function sanitizeQuery(raw) {
  return raw
    .toLowerCase()
    .trim()
    .replace(/^\.+/, '')
    .replace(/[^a-z0-9.-]/g, '');
}

export default function SearchScreen({ t, onSelect }) {
  const [query, setQuery] = useState('');
  const [zones, setZones] = useState([]);
  const [selectedZones, setSelectedZones] = useState([]);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);
  const timerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    api
      .zones()
      .then((list) => {
        if (!cancelled) setZones(Array.isArray(list) ? list : []);
      })
      .catch(() => {
        // A failed zone list must not block search: the backend falls back to
        // its own defaults when we send none.
        if (!cancelled) setZones([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runSearch = useCallback(
    async (rawQuery, tlds) => {
      const clean = sanitizeQuery(rawQuery);
      if (!clean) {
        setResults(null);
        setError(null);
        return;
      }

      // Cancel the in-flight request: its answer is already stale.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setLoading(true);
      setError(null);
      try {
        const data = await api.search(clean, tlds, controller.signal);
        setResults(data?.results ?? []);
      } catch (cause) {
        if (cause?.name === 'AbortError') return;
        if (cause instanceof ApiError && cause.status === 422) {
          setError(t('search.invalid'));
        } else if (cause instanceof ApiError && cause.status === 0) {
          setError(t('common.network'));
        } else {
          setError(t('buy.registrar_down'));
        }
        setResults(null);
      } finally {
        if (abortRef.current === controller) setLoading(false);
      }
    },
    [t],
  );

  useEffect(() => {
    clearTimeout(timerRef.current);
    if (!query.trim()) {
      setResults(null);
      return undefined;
    }
    timerRef.current = setTimeout(
      () => runSearch(query, selectedZones),
      DEBOUNCE_MS,
    );
    return () => clearTimeout(timerRef.current);
  }, [query, selectedZones, runSearch]);

  useEffect(() => () => abortRef.current?.abort(), []);

  function toggleZone(zone) {
    haptic('light');
    setSelectedZones((current) =>
      current.includes(zone)
        ? current.filter((z) => z !== zone)
        : [...current, zone],
    );
  }

  const available = (results ?? []).filter((r) => r.available);
  const taken = (results ?? []).filter((r) => !r.available);

  return (
    <div className="space-y-4">
      <div className="relative">
        <input
          className="input pr-11"
          type="text"
          inputMode="url"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck="false"
          placeholder={t('search.placeholder')}
          aria-label={t('search.placeholder')}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              clearTimeout(timerRef.current);
              runSearch(query, selectedZones);
            }
          }}
        />
        <div className="absolute right-4 top-1/2 -translate-y-1/2 text-muted">
          {loading ? <Spinner /> : <span aria-hidden="true">⌕</span>}
        </div>
      </div>

      {zones.length > 0 ? (
        <div className="no-scrollbar -mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
          <button
            type="button"
            onClick={() => setSelectedZones([])}
            className={`badge shrink-0 border px-3 py-1.5 ${
              selectedZones.length === 0
                ? 'border-accent/50 bg-accent/15 text-accent'
                : 'border-line bg-surface text-muted'
            }`}
          >
            {t('search.all_zones')}
          </button>
          {zones.map((zone) => (
            <button
              key={zone}
              type="button"
              onClick={() => toggleZone(zone)}
              className={`badge shrink-0 border px-3 py-1.5 ${
                selectedZones.includes(zone)
                  ? 'border-accent/50 bg-accent/15 text-accent'
                  : 'border-line bg-surface text-muted'
              }`}
            >
              .{zone}
            </button>
          ))}
        </div>
      ) : null}

      {error ? (
        <ErrorNote
          message={error}
          retryLabel={t('common.retry')}
          onRetry={() => runSearch(query, selectedZones)}
        />
      ) : null}

      {loading && !results ? <SkeletonList rows={4} /> : null}

      {!loading && !error && results === null ? (
        <EmptyState
          icon="⌕"
          title={t('search.start')}
          hint={t('search.start_hint')}
        />
      ) : null}

      {results !== null && results.length === 0 && !loading ? (
        <EmptyState icon="∅" title={t('search.empty')} />
      ) : null}

      {available.length > 0 ? (
        <ul className="space-y-2">
          {available.map((result) => (
            <ResultRow
              key={result.domain}
              result={result}
              t={t}
              onSelect={() => {
                haptic('medium');
                onSelect(result);
              }}
            />
          ))}
        </ul>
      ) : null}

      {taken.length > 0 ? (
        <ul className="space-y-2 opacity-55">
          {taken.map((result) => (
            <ResultRow key={result.domain} result={result} t={t} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ResultRow({ result, t, onSelect }) {
  const buyable = result.available && onSelect;
  // Format in the active language: a RU-grouped number in an EN session is a
  // visible inconsistency on the most important element of the screen.
  const price = formatPrice(result.price_rub, t.language);
  return (
    <li>
      <div className="card flex items-center justify-between gap-3 px-4 py-3">
        <div className="min-w-0">
          <p className="truncate font-medium">{result.domain}</p>
          <p className="text-xs text-muted">
            {result.available ? t('search.available') : t('search.taken')}
            {result.is_premium ? ` · ${t('search.premium')}` : ''}
          </p>
        </div>

        {buyable ? (
          <button
            type="button"
            onClick={onSelect}
            className="btn-primary shrink-0 px-3 py-2 text-sm"
            aria-label={`${result.domain} — ${price} RUB`}
          >
            {price} ₽
          </button>
        ) : (
          <span className="shrink-0 text-sm text-faint">—</span>
        )}
      </div>
    </li>
  );
}
