/** Layout primitives shared by the TMA and the admin panel. */
import { useEffect, useRef, useState } from 'react';

import { THEMES, themeMeta } from '../lib/themes';

/** The 'D' monogram.
 *
 * A single flat glyph rather than a logo image: it stays crisp at any density,
 * costs no extra request, and inherits the accent so the light/dark Telegram
 * themes both get correct contrast.
 */
export function Monogram({ className = '' }) {
  return (
    <span
      aria-hidden="true"
      className={`grid h-6 w-6 shrink-0 place-items-center rounded-md
        bg-accent text-[13px] font-semibold leading-none text-accent-ink
        ${className}`}
    >
      D
    </span>
  );
}

/** Wordmark: monogram plus product name, used in both app headers. */
export function Brand({ label = 'Dynadot', subtitle = null }) {
  return (
    <div className="flex items-center gap-2">
      <Monogram />
      <span className="text-sm font-semibold tracking-tight text-ink">
        {label}
      </span>
      {subtitle ? (
        <span className="text-xs text-faint">{subtitle}</span>
      ) : null}
    </div>
  );
}

/** One-tap RU/EN switch.
 *
 * Shows the language it switches *to*, not the active one: the label is the
 * action, which is what makes it a single unambiguous tap.
 */
export function LangToggle({ language, onToggle, label = 'Switch language' }) {
  const target = language === 'ru' ? 'en' : 'ru';
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={label}
      className="rounded-md border border-line bg-surface px-2 py-1 text-xs
        font-medium tabular-nums text-muted transition-colors
        hover:border-faint hover:text-ink"
    >
      {target.toUpperCase()}
    </button>
  );
}

/** Ultrathin 48px app bar shared by the TMA and admin panel. */
export function AppBar({ children }) {
  return (
    <header
      className="sticky top-0 z-30 h-12 border-b border-line bg-bg/90
        backdrop-blur"
    >
      {children}
    </header>
  );
}

export function Spinner({ className = 'h-4 w-4' }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="3"
      />
      <path
        className="opacity-90"
        fill="currentColor"
        d="M12 2a10 10 0 0 1 10 10h-3a7 7 0 0 0-7-7V2Z"
      />
    </svg>
  );
}

/** Placeholder rows that match the real layout, so loading does not reflow. */
export function SkeletonList({ rows = 4 }) {
  return (
    <div className="space-y-2" data-testid="skeleton">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="skeleton h-16 rounded-xl" />
      ))}
    </div>
  );
}

export function EmptyState({ icon = '○', title, hint, action = null }) {
  return (
    <div className="flex flex-col items-center gap-2 py-14 text-center">
      <div className="mb-1 text-2xl text-faint" aria-hidden="true">
        {icon}
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint ? <p className="max-w-xs text-sm text-muted">{hint}</p> : null}
      {action}
    </div>
  );
}

/** Inline error with a retry affordance.
 *
 * A dead-end error message in a purchase flow loses the sale; every failure
 * the user can recover from gets a button.
 */
export function ErrorNote({ message, onRetry, retryLabel = 'Retry' }) {
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-xl border
        border-danger/30 bg-danger/10 px-4 py-3 text-sm text-ink"
    >
      <span>{message}</span>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="shrink-0 font-medium text-danger">
          {retryLabel}
        </button>
      ) : null}
    </div>
  );
}

export function Toast({ message, tone = 'ok' }) {
  if (!message) return null;
  const tones = {
    ok: 'border-ok/30 bg-surface/95 text-ink',
    warn: 'border-warn/30 bg-surface/95 text-ink',
    danger: 'border-danger/30 bg-surface/95 text-ink',
  };
  const cleanMessage =
    typeof message === 'string'
      ? message.replace(/^(✓|⚠️|✕)\s*/, '')
      : message;
  return (
    <div
      role="status"
      className={`fixed top-14 left-1/2 -translate-x-1/2 lg:top-6 lg:right-8 lg:left-auto lg:translate-x-0 z-50 animate-in fade-in slide-in-from-top-3 duration-200 rounded-2xl border px-4 py-2.5 text-xs font-medium shadow-2xl backdrop-blur-md max-w-[90vw] sm:max-w-md w-max flex items-center gap-2.5 ${tones[tone] ?? tones.ok}`}
    >
      <span className="text-sm">{tone === 'ok' ? '✓' : tone === 'warn' ? '⚠️' : '✕'}</span>
      <span>{cleanMessage}</span>
    </div>
  );
}

/** Theme picker: a swatch button that opens a popover of the five themes.
 *
 * A popover rather than a cycle-button: with five options a single toggle
 * would take up to four taps to reach a known theme and never shows what is
 * available. Each row paints its own accent swatch, so the choice is made by
 * looking rather than by reading names.
 *
 * Closes on outside click and on Escape — a popover that can only be
 * dismissed by re-tapping the trigger traps the user on a narrow screen.
 */
export function ThemePicker({
  theme,
  onSelect,
  label = 'Theme',
  align = 'right',
  drop = 'down',
  testId = 'theme-trigger',
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const active = themeMeta(theme);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        data-testid={testId}
        className="flex items-center gap-1.5 rounded-lg border border-line
          bg-surface px-2.5 py-1.5 text-xs font-medium text-muted
          transition-colors hover:border-faint hover:text-ink shadow-2xs cursor-pointer"
      >
        <span
          aria-hidden="true"
          className="h-3 w-3 shrink-0 rounded-full border border-line"
          style={{ background: active.accent }}
        />
        <span className="truncate max-w-[100px]">{active.name}</span>
        <span className="text-[10px] text-muted shrink-0">▾</span>
      </button>

      {open ? (
        <div
          role="listbox"
          aria-label={label}
          data-testid={testId === 'theme-trigger' ? 'theme-menu' : `${testId}-menu`}
          className={`absolute ${drop === 'up' ? 'bottom-full mb-2' : 'top-full mt-2'} z-50 w-56 animate-fade-up
            overflow-hidden rounded-xl border border-line bg-surface
            shadow-2xl ${align === 'right' ? 'right-0' : 'left-0'}`}
        >
          {THEMES.map((item) => {
            const selected = item.id === active.id;
            return (
              <button
                key={item.id}
                type="button"
                role="option"
                aria-selected={selected}
                data-theme-option={item.id}
                onClick={() => {
                  onSelect?.(item.id);
                  setOpen(false);
                }}
                className={`flex w-full items-center gap-3 px-3 py-2.5 text-left
                  transition-colors hover:bg-elevated
                  ${selected ? 'bg-elevated' : ''}`}
              >
                <span
                  aria-hidden="true"
                  className="h-5 w-5 shrink-0 rounded-full border border-line"
                  style={{ background: item.accent }}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-ink">
                    {item.name}
                  </span>
                  <span className="block truncate text-xs text-faint">
                    {item.hint}
                  </span>
                </span>
                {selected ? (
                  <span aria-hidden="true" className="text-xs text-accent">
                    ✓
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
