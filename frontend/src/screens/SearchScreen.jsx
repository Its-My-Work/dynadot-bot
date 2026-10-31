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

const RU_MAP = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'yo', ж: 'zh', з: 'z',
  и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r',
  с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts', ч: 'ch', ш: 'sh',
  щ: 'shch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya', ' ': '',
};

/** Strip anything that cannot be in a domain label, transliterating Russian Cyrillic automatically. */
export function sanitizeQuery(raw) {
  let text = String(raw || '').toLowerCase().trim();
  text = text.replace(/[а-яё ]/g, (ch) => (RU_MAP[ch] !== undefined ? RU_MAP[ch] : ch));
  return text
    .replace(/^\.+/, '')
    .replace(/[^a-z0-9.-]/g, '');
}

const POPULAR_AI_ZONES = ['com', 'pro', 'io', 'ai', 'co', 'store', 'tech', 'online', 'space', 'site'];

export default function SearchScreen({ t, onSelect }) {
  const [query, setQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  const [results, setResults] = useState(null);
  const [alternatives, setAlternatives] = useState([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);

  // AI Brand Drawer (Option 1)
  const [aiOpen, setAiOpen] = useState(false);
  const [aiConceptInput, setAiConceptInput] = useState('');
  const [selectedAiZones, setSelectedAiZones] = useState([]);
  const [showAiCatalog, setShowAiCatalog] = useState(false);
  const [aiCustomZoneInput, setAiCustomZoneInput] = useState('');
  const [aiSuggestions, setAiSuggestions] = useState([]);
  const [aiKeywords, setAiKeywords] = useState([]);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiPollingProgress, setAiPollingProgress] = useState(null);
  const aiPollingAbortRef = useRef(null);

  // Catalog modal
  const [showCatalog, setShowCatalog] = useState(false);
  const [catalogItems, setCatalogItems] = useState([]);
  const [catalogSearch, setCatalogSearch] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(false);

  // Client-side advanced filters on results
  const [filterPriceMax, setFilterPriceMax] = useState(0); // 0 = all
  const [filterSort, setFilterSort] = useState('default'); // default, cheap, expensive, az
  const [filterTldQuery, setFilterTldQuery] = useState('');
  const [isTyping, setIsTyping] = useState(false);

  const abortRef = useRef(null);
  const timerRef = useRef(null);
  const offsetRef = useRef(0);
  const lastSearchedRef = useRef({ query: '', category: '' });
  const sentinelRef = useRef(null);

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
        setIsTyping(false);
        lastSearchedRef.current = { query: '', category: '' };
        return;
      }

      setIsTyping(false);

      const controller = new AbortController();
      abortRef.current?.abort();
      abortRef.current = controller;

      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
        setError(null);
        offsetRef.current = 0;
        // If searching a new domain name, clear old results so user never sees stale data
        if (clean !== lastSearchedRef.current.query) {
          setResults(null);
          setAlternatives([]);
        }
      }

      try {
        const currentOffset = append ? offsetRef.current : 0;
        const res = await api.search(clean, null, controller.signal, {
          category: category !== 'all' ? category : null,
          offset: currentOffset,
          limit: 15,
        });

        if (abortRef.current !== controller) return;

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
          lastSearchedRef.current = { query: clean, category };
          offsetRef.current = newItems.length;
        }
        setHasMore(Boolean(res?.has_more));
      } catch (err) {
        if (abortRef.current !== controller || err.name === 'AbortError') return;
        if (err instanceof ApiError && err.status === 422) {
          setError(t('search.invalid'));
        } else if (err instanceof ApiError && err.status === 0) {
          setError(t('common.network'));
        } else {
          setError(t('buy.registrar_down'));
        }
      } finally {
        if (abortRef.current === controller) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [t],
  );

  useEffect(() => {
    clearTimeout(timerRef.current);
    const clean = sanitizeQuery(query);
    if (!clean) {
      setIsTyping(false);
      setResults(null);
      setAlternatives([]);
      setHasMore(false);
      setError(null);
      setLoading(false);
      lastSearchedRef.current = { query: '', category: '' };
      return;
    }

    if (
      clean === lastSearchedRef.current.query &&
      activeCategory === lastSearchedRef.current.category
    ) {
      setIsTyping(false);
      return;
    }

    setIsTyping(true);
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

  // Auto-scroll infinite loading observer
  useEffect(() => {
    if (!hasMore || loadingMore || loading) return;
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          runSearch(query, activeCategory, true);
        }
      },
      { rootMargin: '300px' },
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, loadingMore, loading, query, activeCategory, runSearch]);

  const stopAiPolling = () => {
    haptic('medium');
    aiPollingAbortRef.current?.abort();
    aiPollingAbortRef.current = null;
    setAiLoading(false);
    setAiPollingProgress(null);
  };

  const handleAiGenerate = async () => {
    const raw = aiConceptInput.trim();
    if (!raw) return;
    haptic('medium');

    aiPollingAbortRef.current?.abort();
    const controller = new AbortController();
    aiPollingAbortRef.current = controller;

    setAiLoading(true);
    setAiSuggestions([]);
    setAiKeywords([]);
    setAiPollingProgress(null);

    const zones = selectedAiZones.length > 0 ? selectedAiZones : POPULAR_AI_ZONES;
    const isMultiBatch = zones.length > 15;
    const initialBatch = zones.slice(0, 15);

    try {
      const res = await api.aiSuggest(raw, initialBatch, [], controller.signal);
      if (controller.signal.aborted) return;

      const candidates = res?.candidates || [];
      const keywords = res?.keywords || [];
      const initialSuggestions = res?.suggestions || [];

      setAiKeywords(keywords);
      setAiSuggestions(initialSuggestions);

      if (isMultiBatch && candidates.length > 0) {
        const roots = Array.from(new Set(candidates.map((c) => c.split('.')[0])));
        const remainingZones = zones.slice(15);
        const chunkSize = 20;
        let checkedCount = initialBatch.length;

        setAiPollingProgress({ checked: checkedCount, total: zones.length });

        for (let i = 0; i < remainingZones.length; i += chunkSize) {
          if (controller.signal.aborted) break;
          const chunk = remainingZones.slice(i, i + chunkSize);

          const chunkCandidates = [];
          for (const root of roots.slice(0, 2)) {
            for (const tld of chunk) {
              chunkCandidates.push(`${root}.${tld}`);
            }
          }

          try {
            await new Promise((resolve) => setTimeout(resolve, 600));
            if (controller.signal.aborted) break;

            const chunkRes = await api.aiSuggest(raw, chunk, chunkCandidates, controller.signal);
            if (controller.signal.aborted) break;

            if (chunkRes?.suggestions?.length > 0) {
              setAiSuggestions((prev) => {
                const existing = new Set(prev.map((s) => s.domain));
                const newOnes = chunkRes.suggestions.filter((s) => !existing.has(s.domain));
                return [...prev, ...newOnes];
              });
            }
          } catch {
            // continue next chunk
          }

          checkedCount += chunk.length;
          setAiPollingProgress({ checked: Math.min(checkedCount, zones.length), total: zones.length });
        }
      }
    } catch {
      // Aborted or error
    } finally {
      if (aiPollingAbortRef.current === controller) {
        setAiLoading(false);
        setAiPollingProgress(null);
        aiPollingAbortRef.current = null;
      }
    }
  };

  const toggleAiZone = (zone) => {
    haptic('light');
    setSelectedAiZones((prev) =>
      prev.includes(zone) ? prev.filter((z) => z !== zone) : [...prev, zone],
    );
  };

  const handleOpenAiCatalog = async () => {
    haptic('light');
    setShowAiCatalog(true);
    if (catalogItems.length === 0) {
      setCatalogLoading(true);
      try {
        const res = await api.catalog();
        setCatalogItems(Array.isArray(res) ? res : (res?.items || []));
      } catch {
        setCatalogItems([]);
      } finally {
        setCatalogLoading(false);
      }
    }
  };

  const handleAddCustomZone = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const clean = aiCustomZoneInput.trim().toLowerCase().replace(/^\.+/, '');
      if (clean && !selectedAiZones.includes(clean)) {
        haptic('light');
        setSelectedAiZones((prev) => [...prev, clean]);
        setAiCustomZoneInput('');
      }
    }
  };

  const hasActiveFilters =
    filterPriceMax > 0 || filterSort !== 'default' || Boolean(filterTldQuery.trim());

  const resetFilters = () => {
    haptic('light');
    setFilterPriceMax(0);
    setFilterSort('default');
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
          onChange={(event) => {
            setQuery(event.target.value);
            const clean = sanitizeQuery(event.target.value);
            if (clean && clean !== lastSearchedRef.current.query) {
              setIsTyping(true);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              clearTimeout(timerRef.current);
              setIsTyping(false);
              runSearch(query, activeCategory, false);
            }
          }}
        />
        <div className="absolute right-4 top-1/2 -translate-y-1/2 text-muted">
          {loading || isTyping ? <Spinner /> : <span aria-hidden="true">⌕</span>}
        </div>
      </div>

      {/* Smart AI Brand Generator Drawer (Option 1) */}
      <div className="rounded-xl border border-accent/25 bg-accent/5 overflow-hidden transition-all shadow-2xs">
        <button
          type="button"
          onClick={() => {
            haptic('light');
            setAiOpen(!aiOpen);
          }}
          className="w-full flex items-center justify-between p-3 cursor-pointer text-left hover:bg-accent/10 transition-colors"
        >
          <div className="flex items-center gap-2">
            <span className="text-base">✨</span>
            <div>
              <p className="text-xs font-bold text-accent">
                {t('search.ai_drawer_title')}
              </p>
              <p className="text-[11px] text-muted">
                {t('search.ai_drawer_subtitle')}
              </p>
            </div>
          </div>
          <span className={`text-accent text-xs font-bold transition-transform duration-200 ${aiOpen ? 'rotate-180' : ''}`}>
            ▾
          </span>
        </button>

        {aiOpen && (
          <div className="p-3 pt-0 border-t border-accent/15 space-y-3 mt-1">
            <div className="flex gap-2 pt-2">
              <input
                type="text"
                value={aiConceptInput}
                onChange={(e) => setAiConceptInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleAiGenerate();
                  }
                }}
                placeholder={t('search.ai_input_placeholder')}
                className="input text-xs flex-1 py-2 px-3 bg-card border-card-border"
              />
              <button
                type="button"
                disabled={aiLoading || !aiConceptInput.trim()}
                onClick={handleAiGenerate}
                className="btn-accent px-3 py-2 text-xs font-semibold shrink-0 cursor-pointer flex items-center gap-1.5 shadow-xs"
              >
                {aiLoading ? <Spinner /> : <span>✨</span>}
                <span>{t('search.ai_generate_btn')}</span>
              </button>
            </div>

            {/* Target Zones Selector */}
            <div className="space-y-2 pt-0.5">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted font-medium">{t('search.ai_zones_label')}</span>
                <div className="flex items-center gap-2">
                  {selectedAiZones.length > 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        haptic('light');
                        setSelectedAiZones([]);
                      }}
                      className="text-[10px] text-accent font-semibold hover:underline cursor-pointer"
                    >
                      {t('search.ai_zones_all')}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={handleOpenAiCatalog}
                    className="badge border border-accent/40 bg-accent/10 hover:bg-accent/20 text-accent font-bold px-2 py-0.5 text-[11px] cursor-pointer flex items-center gap-1 transition-colors"
                  >
                    <span>{t('search.ai_zones_custom_btn')}</span>
                  </button>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => {
                    haptic('light');
                    setSelectedAiZones([]);
                  }}
                  className={`badge px-2.5 py-1 text-xs cursor-pointer border transition-colors ${
                    selectedAiZones.length === 0
                      ? 'border-accent bg-accent/20 text-accent font-bold shadow-2xs'
                      : 'border-card-border bg-card text-muted hover:border-accent hover:text-ink'
                  }`}
                >
                  {t('search.ai_zones_all')}
                </button>
                {POPULAR_AI_ZONES.map((zone) => {
                  const isChecked = selectedAiZones.includes(zone);
                  return (
                    <button
                      key={zone}
                      type="button"
                      onClick={() => toggleAiZone(zone)}
                      className={`badge px-2.5 py-1 text-xs cursor-pointer border font-mono transition-colors ${
                        isChecked
                          ? 'border-accent bg-accent/20 text-accent font-bold shadow-2xs'
                          : 'border-card-border bg-card text-muted hover:border-accent hover:text-ink'
                      }`}
                    >
                      .{zone}
                    </button>
                  );
                })}

                {/* Custom selected zones from 800+ catalog */}
                {selectedAiZones.length > 10 ? (
                  <span className="badge px-2.5 py-1 text-xs border border-accent bg-accent/20 text-accent font-bold flex items-center gap-1.5 shadow-2xs">
                    <span>✓ Выбрано зон: {selectedAiZones.length}</span>
                    <button
                      type="button"
                      onClick={() => setSelectedAiZones([])}
                      className="hover:text-ink cursor-pointer text-xs ml-1"
                      title="Сбросить"
                    >
                      ✕
                    </button>
                  </span>
                ) : (
                  selectedAiZones
                    .filter((z) => !POPULAR_AI_ZONES.includes(z))
                    .map((customZone) => (
                      <span
                        key={customZone}
                        className="badge px-2 py-1 text-xs border border-accent bg-accent/20 text-accent font-bold font-mono flex items-center gap-1.5 shadow-2xs"
                      >
                        .{customZone}
                        <button
                          type="button"
                          onClick={() => toggleAiZone(customZone)}
                          className="hover:text-ink cursor-pointer text-xs"
                        >
                          ✕
                        </button>
                      </span>
                    ))
                )}
              </div>

              {/* Quick inline input to add any zone */}
              <div className="pt-0.5">
                <input
                  type="text"
                  value={aiCustomZoneInput}
                  onChange={(e) => setAiCustomZoneInput(e.target.value)}
                  onKeyDown={handleAddCustomZone}
                  placeholder={t('search.ai_custom_zone_placeholder')}
                  className="input text-[11px] py-1 px-2.5 w-full bg-card/60 border-card-border/70 font-mono placeholder:font-sans"
                />
              </div>
            </div>

            {aiLoading && (
              <div className="rounded-xl border border-accent/30 bg-accent/10 p-3 space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold text-accent flex items-center gap-2">
                    <Spinner />
                    <span>
                      {aiPollingProgress
                        ? t('search.ai_polling_progress', {
                            checked: aiPollingProgress.checked,
                            total: aiPollingProgress.total,
                            count: aiSuggestions.length,
                          })
                        : t('search.ai_loading')}
                    </span>
                  </span>
                  {aiPollingProgress && (
                    <button
                      type="button"
                      onClick={stopAiPolling}
                      className="badge bg-card border-card-border text-[11px] px-2.5 py-1 text-ink hover:text-accent cursor-pointer font-bold transition-colors"
                    >
                      ✕ {t('search.ai_polling_stop')}
                    </button>
                  )}
                </div>
                {aiPollingProgress && (
                  <div className="w-full bg-card/70 h-2 rounded-full overflow-hidden p-0.5 border border-card-border/60">
                    <div
                      className="bg-accent h-full rounded-full transition-all duration-300"
                      style={{
                        width: `${Math.max(5, Math.round((aiPollingProgress.checked / aiPollingProgress.total) * 100))}%`,
                      }}
                    />
                  </div>
                )}
              </div>
            )}

            {/* Tags / Keywords */}
            {aiKeywords.length > 0 && (
              <div className="flex flex-wrap items-center gap-1 pt-1">
                <span className="text-[10px] uppercase font-bold text-muted/70 tracking-wider">
                  {t('search.keywords_label')}:
                </span>
                {aiKeywords.map((kw) => (
                  <span
                    key={kw}
                    className="badge border border-card-border/60 bg-card/80 text-ink text-[11px] px-2 py-0.5 font-medium"
                  >
                    #{kw}
                  </span>
                ))}
              </div>
            )}

            {/* Generated Available Domains Pills */}
            {aiSuggestions.length > 0 && (
              <div className="space-y-1.5 pt-1">
                <p className="text-[11px] text-muted font-medium">
                  {t('search.ai_available_results')}:
                </p>
                <div className="flex flex-wrap gap-2">
                  {aiSuggestions.map((s) => (
                    <button
                      key={s.domain}
                      type="button"
                      onClick={() => {
                        haptic('medium');
                        onSelect(s);
                      }}
                      className="badge cursor-pointer border border-accent/35 bg-card hover:border-accent hover:bg-accent/10 px-3 py-1.5 text-xs font-medium text-ink flex items-center gap-2 shadow-2xs transition-all"
                    >
                      <span className="font-bold text-accent">{s.domain}</span>
                      <span className="text-muted">·</span>
                      <span className="font-semibold text-ink">
                        {Number(s.price_rub).toLocaleString('ru-RU')} ₽
                      </span>
                      <span className="text-accent text-[11px]">🛒</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Categories Bar & Catalog Button */}
      <div className="flex items-center justify-between gap-2 border-b border-card-border/50 pb-2">
        <div className="no-scrollbar -mx-1 flex flex-1 gap-2 overflow-x-auto px-1 py-0.5">
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

        <button
          type="button"
          onClick={openCatalog}
          className="badge shrink-0 cursor-pointer border border-accent/40 bg-card px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/10 transition-colors"
        >
          📚 {t('search.catalog_btn')}
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

      {/* Results Header (Airy, single-line toolbar directly above list) */}
      {results !== null && (
        <div className="flex items-center justify-between text-xs px-1 pt-1">
          <div className="flex items-center gap-2">
            <span className="text-muted font-medium">
              {t('search.results_count', { count: filteredResults.length })}
            </span>
            {loading && (
              <span className="flex items-center gap-1.5 text-accent font-semibold animate-pulse">
                <Spinner />
                {t('search.updating_zones')}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {/* Sort Dropdown */}
            <select
              value={filterSort}
              onChange={(e) => {
                haptic('light');
                setFilterSort(e.target.value);
              }}
              aria-label="Сортировка"
              className="badge py-1 px-2 text-xs bg-card border-card-border text-ink rounded font-medium cursor-pointer"
            >
              <option value="default">{t('search.filter_sort_recommended')}</option>
              <option value="cheap">{t('search.filter_sort_cheap')}</option>
              <option value="expensive">{t('search.filter_sort_expensive')}</option>
              <option value="az">{t('search.filter_sort_az')}</option>
            </select>
          </div>
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

      {/* Infinite Scroll Sentinel / Load More */}
      {hasMore && (
        <div ref={sentinelRef} className="pt-2 pb-4 text-center">
          {loadingMore ? (
            <div className="flex items-center justify-center gap-2 py-3 text-xs text-muted font-medium animate-pulse">
              <Spinner />
              <span>{t('search.loading_more_zones')}</span>
            </div>
          ) : (
            <button
              type="button"
              onClick={loadMore}
              className="btn-ghost w-full py-2 text-xs font-medium text-muted hover:text-ink cursor-pointer border border-card-border/60"
            >
              {t('search.load_more')}
            </button>
          )}
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

      {/* 800+ AI Zone Multi-Select Modal */}
      {showAiCatalog && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-xs sm:items-center p-0 sm:p-4">
          <div className="card flex max-h-[68vh] w-full max-w-md sm:max-w-lg flex-col overflow-hidden rounded-t-2xl sm:rounded-2xl border border-card-border bg-card shadow-2xl">
            <div className="flex items-center justify-between border-b border-card-border p-3.5">
              <div>
                <h3 className="text-sm font-bold text-ink">
                  {t('search.ai_catalog_title')} (800+)
                </h3>
                <p className="text-[11px] text-muted">
                  {t('search.ai_catalog_subtitle')}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowAiCatalog(false)}
                className="text-muted hover:text-ink text-xl font-bold px-2 cursor-pointer"
              >
                ✕
              </button>
            </div>

            <div className="p-3 border-b border-card-border flex gap-2">
              <input
                type="text"
                className="input py-2 text-sm flex-1"
                placeholder={t('search.catalog_search')}
                value={catalogSearch}
                onChange={(e) => setCatalogSearch(e.target.value)}
              />
              {selectedAiZones.length > 0 && (
                <button
                  type="button"
                  onClick={() => {
                    haptic('light');
                    setSelectedAiZones([]);
                  }}
                  className="btn-ghost px-3 py-2 text-xs font-semibold text-accent cursor-pointer"
                >
                  {t('search.filter_reset')}
                </button>
              )}
            </div>

            {/* Quick Actions (Select All / Deselect) */}
            <div className="flex items-center justify-between px-3.5 py-2 bg-canvas/40 border-b border-card-border/60 text-xs">
              <button
                type="button"
                onClick={() => {
                  haptic('light');
                  const filtered = catalogItems.filter((item) =>
                    !catalogSearch || item.tld.toLowerCase().includes(catalogSearch.toLowerCase())
                  );
                  const allSelected = filtered.length > 0 && filtered.every((item) =>
                    selectedAiZones.includes(item.tld.toLowerCase())
                  );
                  if (allSelected) {
                    const filteredSet = new Set(filtered.map((i) => i.tld.toLowerCase()));
                    setSelectedAiZones((prev) => prev.filter((z) => !filteredSet.has(z)));
                  } else {
                    const newZones = new Set([...selectedAiZones, ...filtered.map((i) => i.tld.toLowerCase())]);
                    setSelectedAiZones(Array.from(newZones));
                  }
                }}
                className="font-bold text-accent hover:underline cursor-pointer flex items-center gap-1.5"
              >
                <span>
                  {catalogItems.filter((item) => !catalogSearch || item.tld.toLowerCase().includes(catalogSearch.toLowerCase())).length > 0 &&
                  catalogItems
                    .filter((item) => !catalogSearch || item.tld.toLowerCase().includes(catalogSearch.toLowerCase()))
                    .every((item) => selectedAiZones.includes(item.tld.toLowerCase()))
                    ? t('search.ai_catalog_deselect_all', {
                        count: catalogItems.filter((item) => !catalogSearch || item.tld.toLowerCase().includes(catalogSearch.toLowerCase())).length,
                      })
                    : t('search.ai_catalog_select_all', {
                        count: catalogItems.filter((item) => !catalogSearch || item.tld.toLowerCase().includes(catalogSearch.toLowerCase())).length,
                      })}
                </span>
              </button>
              {selectedAiZones.length > 0 && (
                <span className="text-muted text-[11px] font-mono">
                  {selectedAiZones.length} / {catalogItems.length}
                </span>
              )}
            </div>

            <div className="flex-1 overflow-y-auto p-3 space-y-1">
              {catalogLoading ? (
                <div className="py-8 text-center"><Spinner /></div>
              ) : (
                catalogItems
                  .filter((item) =>
                    !catalogSearch || item.tld.toLowerCase().includes(catalogSearch.toLowerCase())
                  )
                  .map((item) => {
                    const isSelected = selectedAiZones.includes(item.tld.toLowerCase());
                    return (
                      <div
                        key={item.tld}
                        onClick={() => toggleAiZone(item.tld.toLowerCase())}
                        className={`flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 transition-colors ${
                          isSelected ? 'bg-accent/15 border border-accent/30' : 'hover:bg-canvas'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            readOnly
                            className="accent-accent cursor-pointer"
                          />
                          <span className="font-semibold text-ink">.{item.tld}</span>
                          {item.is_featured && (
                            <span className="text-[10px] text-accent font-bold uppercase tracking-wider">★ Топ</span>
                          )}
                        </div>
                        <span className="text-xs text-muted">
                          {t('search.catalog_from')} {formatPrice(item.price_rub, t.language)} ₽
                        </span>
                      </div>
                    );
                  })
              )}
            </div>

            <div className="p-3.5 border-t border-card-border flex items-center justify-between bg-card">
              <span className="text-xs text-muted font-medium">
                {selectedAiZones.length === 0
                  ? t('search.ai_zones_all')
                  : t('search.ai_catalog_selected', { count: selectedAiZones.length })}
              </span>
              <button
                type="button"
                onClick={() => {
                  haptic('medium');
                  setShowAiCatalog(false);
                }}
                className="btn-accent px-6 py-2.5 text-sm font-bold cursor-pointer shadow-md rounded-xl flex items-center gap-2 hover:scale-[1.02] active:scale-[0.98] transition-all"
              >
                <span>{t('search.ai_catalog_apply')}</span>
                <span className="bg-white/20 px-2 py-0.5 rounded-full text-xs font-mono">
                  {selectedAiZones.length === 0 ? 'Все' : selectedAiZones.length}
                </span>
                <span>✓</span>
              </button>
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

  const dotIndex = result.domain.indexOf('.');
  const tldPart = dotIndex !== -1 ? result.domain.slice(dotIndex) : '';

  const isBargain = result.available && !result.is_premium && result.price_rub && result.price_rub <= 700;

  return (
    <li>
      <div className="card flex items-center justify-between gap-3 px-4 py-3">
        <div className="min-w-0 flex-1">
          {/* Domain name as full text + TLD badge on same row */}
          <p className="truncate font-bold text-ink text-base">{result.domain}</p>

          {/* Status + TLD badge row */}
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {tldPart && (
              <span className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-accent/15 text-accent tracking-wide">
                {tldPart}
              </span>
            )}
            {result.is_premium ? (
              <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-warn/15 text-warn">
                <span aria-hidden="true">★</span>
                <span>{t('search.premium')}</span>
              </span>
            ) : result.available ? (
              <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-ok/15 text-ok">
                <span aria-hidden="true">●</span>
                <span>{t('search.available')}</span>
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold bg-card-border/40 text-faint">
                <span aria-hidden="true">✕</span>
                <span>{t('search.taken')}</span>
              </span>
            )}
            {isBargain && (
              <span className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold bg-ok/15 text-ok">
                🔥 {t('search.bargain')}
              </span>
            )}
          </div>
        </div>

        {buyable ? (
          <button
            type="button"
            onClick={onSelect}
            className="btn-primary shrink-0 flex items-center gap-1.5 px-4 py-2 text-sm font-bold"
            aria-label={`${result.domain} — ${price} RUB`}
          >
            <span aria-hidden="true">🛒</span>
            {price} ₽
          </button>
        ) : (
          <span className="shrink-0 text-sm text-faint">—</span>
        )}
      </div>
    </li>
  );
}