/** Search screen: the product's front door.
 *
 * Enriched with:
 * - Category tabs (All, Top, AI & IT, Business, Budget, Creative)
 * - Show more (+15) progressive loading
 * - Smart alternative suggestions for .com if taken
 * - Full 800+ TLD catalog modal with instant search & filter
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import api, { ApiError } from '../lib/api';
import { formatPrice } from '../lib/i18n';
import { haptic } from '../lib/telegram';
import { EmptyState, ErrorNote, SkeletonList, Spinner } from '../components/ui';

const DEBOUNCE_MS = 450;

export const CATEGORIES = [
  { id: 'all', labelKey: 'search.cat.all' },
  { id: 'popular', labelKey: 'search.cat.popular' },
  { id: 'tech', labelKey: 'search.cat.tech' },
  { id: 'business', labelKey: 'search.cat.business' },
  { id: 'budget', labelKey: 'search.cat.budget' },
  { id: 'creative', labelKey: 'search.cat.creative' },
];

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
  const [activeCategory, setActiveCategory] = useState('all');
  const [results, setResults] = useState(null);
  const [alternatives, setAlternatives] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);

  // Catalog modal
  const [showCatalog, setShowCatalog] = useState(false);
  const [catalogItems, setCatalogItems] = useState([]);
  const [catalogSearch, setCatalogSearch] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(false);

  const abortRef = useRef(null);
  const timerRef = useRef(null);
  const offsetRef = useRef(0);

  const runSearch = useCallback(
    async (rawQuery, category, append = false) => {
      const clean = sanitizeQuery(rawQuery);
      if (!clean) {
        setResults(null);
        setAlternatives([]);
        setHasMore(false);
        setError(null);
        setLoading(false);
        setLoadingMore(false);
        return;
      }

      abortRef.current?.abort();
      abortRef.current = new AbortController();

      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
        setError(null);
        offsetRef.current = 0;
      }

      try {
        const currentOffset = append ? offsetRef.current : 0;
        const res = await api.search(clean, null, abortRef.current.signal, {
          category: category !== 'all' ? category : null,
          offset: currentOffset,
          limit: 15,
        });

        const newItems = res?.results ?? [];
        if (append) {
          setResults((prev) => [...(prev || []), ...newItems]);
        } else {
          setResults(newItems);
          setAlternatives(res?.alternatives ?? []);
        }
        setHasMore(Boolean(res?.has_more));
        offsetRef.current = currentOffset + newItems.length;
      } catch (err) {
        if (err.name === 'AbortError') return;
        if (err instanceof ApiError && err.status === 422) {
          setError(t('search.invalid'));
        } else if (err instanceof ApiError && err.status === 0) {
          setError(t('common.network'));
        } else {
          setError(t('buy.registrar_down'));
        }
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [t],
  );

  useEffect(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(
      () => runSearch(query, activeCategory, false),
      DEBOUNCE_MS,
    );
    return () => clearTimeout(timerRef.current);
  }, [query, activeCategory, runSearch]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Open catalog
  const openCatalog = async () => {
    haptic('light');
    setShowCatalog(true);
    if (catalogItems.length === 0) {
      setCatalogLoading(true);
      try {
        const list = await api.catalog();
        setCatalogItems(Array.isArray(list) ? list : []);
      } catch {
        // fallback
      } finally {
        setCatalogLoading(false);
      }
    }
  };

  const loadMore = () => {
    haptic('light');
    runSearch(query, activeCategory, true);
  };

  const handleSelectAlternative = (altDomain) => {
    haptic('medium');
    setQuery(altDomain);
  };

  const handleSelectCatalogTld = (tld) => {
    haptic('medium');
    setShowCatalog(false);
    const base = query ? query.split('.')[0] : 'mysite';
    setQuery(`${base}.${tld}`);
  };

  const available = (results ?? []).filter((r) => r.available);
  const taken = (results ?? []).filter((r) => !r.available);

  return (
    <div className="space-y-4">
      {/* Search Input Bar */}
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
              runSearch(query, activeCategory, false);
            }
          }}
        />
        <div className="absolute right-4 top-1/2 -translate-y-1/2 text-muted">
          {loading ? <Spinner /> : <span aria-hidden="true">⌕</span>}
        </div>
      </div>

      {/* Categories & Catalog Quick Actions */}
      <div className="flex items-center justify-between gap-2">
        <div className="no-scrollbar -mx-1 flex flex-1 gap-2 overflow-x-auto px-1 pb-1">
          {CATEGORIES.map((cat) => {
            const isSelected = activeCategory === cat.id;
            return (
              <button
                key={cat.id}
                type="button"
                onClick={() => {
                  haptic('light');
                  setActiveCategory(cat.id);
                }}
                className={`badge shrink-0 cursor-pointer border px-3 py-1.5 font-medium transition-colors ${
                  isSelected
                    ? 'border-accent bg-accent text-accent-ink font-semibold shadow-sm'
                    : 'border-card-border bg-card text-muted hover:border-accent hover:text-ink'
                }`}
              >
                {t(cat.labelKey)}
              </button>
            );
          })}
        </div>

        <button
          type="button"
          onClick={openCatalog}
          className="badge shrink-0 cursor-pointer border border-accent/40 bg-card px-2.5 py-1.5 text-xs font-medium text-accent hover:bg-accent/10"
        >
          {t('search.catalog_btn')}
        </button>
      </div>

      {/* Error state */}
      {error ? (
        <ErrorNote
          message={error}
          retryLabel={t('common.retry')}
          onRetry={() => runSearch(query, activeCategory, false)}
        />
      ) : null}

      {/* Loading Skeleton */}
      {loading && !results ? <SkeletonList rows={4} /> : null}

      {/* Empty State / Initial */}
      {!loading && !error && results === null ? (
        <EmptyState
          icon="⌕"
          title={t('search.start')}
          hint={t('search.start_hint')}
        />
      ) : null}

      {/* No results */}
      {results !== null && results.length === 0 && !loading ? (
        <EmptyState icon="∅" title={t('search.empty')} />
      ) : null}

      {/* Smart .com Alternatives Suggestion */}
      {alternatives.length > 0 && (
        <div className="card space-y-2 border-accent/30 bg-accent/5 p-3">
          <p className="text-xs font-semibold text-accent">
            {t('search.alternatives_title')}
          </p>
          <div className="flex flex-wrap gap-2">
            {alternatives.map((alt) => (
              <button
                key={alt}
                type="button"
                onClick={() => handleSelectAlternative(alt)}
                className="badge cursor-pointer border border-accent/30 bg-card px-2.5 py-1 text-xs font-medium hover:border-accent hover:text-accent"
              >
                {alt} ↗
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Available Domains List */}
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

      {/* Taken Domains List */}
      {taken.length > 0 ? (
        <ul className="space-y-2 opacity-55">
          {taken.map((result) => (
            <ResultRow key={result.domain} result={result} t={t} />
          ))}
        </ul>
      ) : null}

      {/* Load More Button */}
      {hasMore && (
        <div className="pt-2 text-center">
          <button
            type="button"
            disabled={loadingMore}
            onClick={loadMore}
            className="btn-secondary w-full py-2.5 text-sm font-medium"
          >
            {loadingMore ? <Spinner /> : t('search.load_more')}
          </button>
        </div>
      )}

      {/* 800+ Catalog Modal */}
      {showCatalog && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-xs sm:items-center p-0 sm:p-4">
          <div className="card flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-t-2xl sm:rounded-2xl border border-card-border bg-card shadow-2xl">
            <div className="flex items-center justify-between border-b border-card-border p-4">
              <h3 className="text-base font-semibold text-ink">
                {t('search.catalog_title')} (800+)
              </h3>
              <button
                type="button"
                onClick={() => setShowCatalog(false)}
                className="text-muted hover:text-ink text-xl font-bold px-2"
              >
                ✕
              </button>
            </div>

            <div className="p-3 border-b border-card-border">
              <input
                type="text"
                className="input py-2 text-sm"
                placeholder={t('search.catalog_search')}
                value={catalogSearch}
                onChange={(e) => setCatalogSearch(e.target.value)}
              />
            </div>

            <div className="flex-1 overflow-y-auto p-3 space-y-1">
              {catalogLoading ? (
                <div className="py-8 text-center"><Spinner /></div>
              ) : (
                catalogItems
                  .filter((item) =>
                    !catalogSearch || item.tld.toLowerCase().includes(catalogSearch.toLowerCase())
                  )
                  .map((item) => (
                    <div
                      key={item.tld}
                      onClick={() => handleSelectCatalogTld(item.tld)}
                      className="flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 hover:bg-canvas transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-ink">.{item.tld}</span>
                        {item.is_featured && (
                          <span className="text-[10px] text-accent font-bold uppercase tracking-wider">★ Топ</span>
                        )}
                      </div>
                      <span className="text-xs text-muted">
                        {t('search.catalog_from')} {formatPrice(item.price_rub, t.language)} ₽
                      </span>
                    </div>
                  ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ResultRow({ result, t, onSelect }) {
  const buyable = result.available && onSelect;
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