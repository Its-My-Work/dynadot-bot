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

  // Client-side advanced filters on results
  const [showFilters, setShowFilters] = useState(false);
  const [filterPriceMax, setFilterPriceMax] = useState(0); // 0 = all
  const [filterSort, setFilterSort] = useState('default'); // default, cheap, expensive, az
  const [filterOnlyAvail, setFilterOnlyAvail] = useState(false);
  const [filterTldQuery, setFilterTldQuery] = useState('');

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
          setResults((prev) => {
            const existing = new Set((prev || []).map((x) => x.domain));
            const fresh = newItems.filter((x) => !existing.has(x.domain));
            return [...(prev || []), ...fresh];
          });
          offsetRef.current = currentOffset + newItems.length;
        } else {
          setResults(newItems);
          setAlternatives(res?.alternatives ?? []);
          offsetRef.current = newItems.length;
        }
        setHasMore(Boolean(res?.has_more));
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

  const handleCategoryChange = (catId) => {
    haptic('light');
    if (activeCategory === catId) return;
    setActiveCategory(catId);
    setLoading(true); // immediate feedback
    clearTimeout(timerRef.current);
    runSearch(query, catId, false);
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

  // Apply client filters and sorting
  let filteredResults = (results ?? []).filter((r) => {
    if (filterOnlyAvail && !r.available) return false;
    if (filterPriceMax > 0 && r.price_rub && r.price_rub > filterPriceMax) return false;
    if (filterTldQuery.trim()) {
      const cleanTld = filterTldQuery.trim().toLowerCase().replace(/^\.+/, '');
      if (!r.tld.toLowerCase().includes(cleanTld)) return false;
    }
    return true;
  });

  if (filterSort === 'cheap') {
    filteredResults = [...filteredResults].sort(
      (a, b) => (a.price_rub || 999999) - (b.price_rub || 999999),
    );
  } else if (filterSort === 'expensive') {
    filteredResults = [...filteredResults].sort(
      (a, b) => (b.price_rub || 0) - (a.price_rub || 0),
    );
  } else if (filterSort === 'az') {
    filteredResults = [...filteredResults].sort((a, b) =>
      a.tld.localeCompare(b.tld),
    );
  }

  const available = filteredResults.filter((r) => r.available);
  const taken = filteredResults.filter((r) => !r.available);

  const hasActiveFilters =
    filterPriceMax > 0 || filterSort !== 'default' || filterOnlyAvail || Boolean(filterTldQuery.trim());

  const resetFilters = () => {
    haptic('light');
    setFilterPriceMax(0);
    setFilterSort('default');
    setFilterOnlyAvail(false);
    setFilterTldQuery('');
  };

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

      {/* Categories & Results Controls Header (above results list) */}
      <div className="space-y-2 pt-1">
        {/* Top actions bar: Filters toggle & Catalog */}
        <div className="flex items-center justify-between gap-2 border-b border-card-border/50 pb-2">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => {
                haptic('light');
                setShowFilters(!showFilters);
              }}
              className={`badge cursor-pointer border px-3 py-1.5 text-xs font-medium transition-colors ${
                showFilters || hasActiveFilters
                  ? 'border-accent bg-accent text-accent-ink font-semibold shadow-xs'
                  : 'border-card-border bg-card text-muted hover:border-accent hover:text-ink'
              }`}
            >
              ⚙ {t('search.filter_btn')}
              {hasActiveFilters && <span className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-accent-ink animate-pulse" />}
            </button>

            {hasActiveFilters && (
              <button
                type="button"
                onClick={resetFilters}
                className="text-xs text-muted hover:text-accent underline px-1 cursor-pointer"
              >
                {t('search.filter_reset')}
              </button>
            )}
          </div>

          <button
            type="button"
            onClick={openCatalog}
            className="badge cursor-pointer border border-accent/40 bg-card px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/10 transition-colors"
          >
            📚 {t('search.catalog_btn')}
          </button>
        </div>

        {/* Categories scrollable pill chips with loading spinner when changing category */}
        <div className="relative">
          <div className="no-scrollbar -mx-1 flex gap-2 overflow-x-auto px-1 py-1">
            {CATEGORIES.map((cat) => {
              const isSelected = activeCategory === cat.id;
              return (
                <button
                  key={cat.id}
                  type="button"
                  onClick={() => handleCategoryChange(cat.id)}
                  className={`badge shrink-0 cursor-pointer border px-3 py-1.5 text-xs font-medium transition-colors ${
                    isSelected
                      ? 'border-accent bg-accent/20 text-accent font-semibold border-2 shadow-xs'
                      : 'border-card-border bg-card text-muted hover:border-accent hover:text-ink'
                  }`}
                >
                  {t(cat.labelKey)}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Advanced Filter Panel (collapsible) */}
      {showFilters && (
        <div className="card space-y-3 border-card-border bg-card/70 p-3.5 text-xs">
          <div className="flex items-center justify-between border-b border-card-border/60 pb-2">
            <span className="font-semibold text-ink">Настройки фильтрации</span>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={resetFilters}
                className="text-accent hover:underline font-medium cursor-pointer"
              >
                {t('search.filter_reset')}
              </button>
            )}
          </div>

          {/* Filter by TLD */}
          <div>
            <label className="mb-1 block text-muted">Поиск конкретной зоны:</label>
            <input
              type="text"
              placeholder={t('search.filter_tld_search')}
              value={filterTldQuery}
              onChange={(e) => setFilterTldQuery(e.target.value)}
              className="input py-1.5 text-xs"
            />
          </div>

          {/* Price Range Filter */}
          <div>
            <label className="mb-1.5 block text-muted">Ограничение по стоимости:</label>
            <div className="flex flex-wrap gap-1.5">
              {[
                { max: 0, label: t('search.filter_all_prices') },
                { max: 700, label: t('search.filter_under_700') },
                { max: 1500, label: t('search.filter_under_1500') },
                { max: 3000, label: t('search.filter_under_3000') },
              ].map((p) => (
                <button
                  key={p.max}
                  type="button"
                  onClick={() => {
                    haptic('light');
                    setFilterPriceMax(p.max);
                  }}
                  className={`badge cursor-pointer border px-2.5 py-1 transition-colors ${
                    filterPriceMax === p.max
                      ? 'border-accent bg-accent text-accent-ink font-semibold'
                      : 'border-card-border bg-card text-muted hover:border-accent'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          {/* Sort & Availability Row */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pt-1 border-t border-card-border/60">
            <div>
              <label className="mb-1 block text-muted">Сортировка:</label>
              <select
                value={filterSort}
                onChange={(e) => {
                  haptic('light');
                  setFilterSort(e.target.value);
                }}
                className="input py-1.5 text-xs bg-card"
              >
                <option value="default">{t('search.filter_sort_recommended')}</option>
                <option value="cheap">{t('search.filter_sort_cheap')}</option>
                <option value="expensive">{t('search.filter_sort_expensive')}</option>
                <option value="az">{t('search.filter_sort_az')}</option>
              </select>
            </div>

            <div className="flex items-center sm:justify-end gap-2 pt-3 sm:pt-4">
              <label className="flex items-center gap-2 cursor-pointer text-ink font-medium">
                <input
                  type="checkbox"
                  checked={filterOnlyAvail}
                  onChange={(e) => {
                    haptic('light');
                    setFilterOnlyAvail(e.target.checked);
                  }}
                  className="rounded border-card-border text-accent focus:ring-accent"
                />
                {t('search.filter_only_available')}
              </label>
            </div>
          </div>
        </div>
      )}

      {/* Error state */}
      {error ? (
        <ErrorNote
          message={error}
          retryLabel={t('common.retry')}
          onRetry={() => runSearch(query, activeCategory, false)}
        />
      ) : null}

      {/* Results Header with count and updating indicator */}
      {results !== null && (
        <div className="flex items-center justify-between text-xs text-muted px-1 py-1 bg-card/40 rounded-lg border border-card-border/40">
          <span className="font-medium">
            {t('search.results_count', { count: filteredResults.length })}
          </span>
          {loading && (
            <span className="flex items-center gap-1.5 text-accent font-semibold animate-pulse">
              <Spinner />
              {t('search.updating_zones')}
            </span>
          )}
        </div>
      )}

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

      {/* Domain Results List with subtle dimming when updating */}
      <div className={`space-y-4 transition-opacity duration-150 ${loading ? 'opacity-50' : 'opacity-100'}`}>
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
      </div>

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