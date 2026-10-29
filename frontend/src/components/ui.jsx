/** Layout primitives shared by the TMA and the admin panel. */

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
export function LangToggle({ language, onToggle }) {
  const next = language === 'ru' ? 'en' : 'ru';
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label="Switch language"
      className="rounded-md border border-line bg-surface px-2 py-1 text-xs
        font-medium tabular-nums text-muted transition-colors
        hover:border-faint hover:text-ink"
    >
      {next.toUpperCase()}
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
    ok: 'border-ok/30 bg-ok/10',
    warn: 'border-warn/30 bg-warn/10',
    danger: 'border-danger/30 bg-danger/10',
  };
  return (
    <div
      role="status"
      className={`fixed inset-x-4 bottom-20 z-50 animate-fade-up rounded-xl border
        px-4 py-3 text-sm text-ink shadow-pop ${tones[tone] ?? tones.ok}`}
    >
      {message}
    </div>
  );
}
