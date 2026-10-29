/** Order/domain status chip.
 *
 * Colour carries meaning here: REFUND_REQUIRED is amber rather than red
 * because it is money owed and actionable, not a dead end — operators and
 * customers read those two states very differently.
 */

const TONES = {
  completed: 'bg-ok/15 text-ok',
  active: 'bg-ok/15 text-ok',
  paid: 'bg-accent/15 text-accent',
  registering: 'bg-accent/15 text-accent',
  awaiting_payment: 'bg-warn/15 text-warn',
  pending: 'bg-warn/15 text-warn',
  refund_required: 'bg-warn/20 text-warn',
  refunded: 'bg-muted/15 text-muted',
  draft: 'bg-muted/15 text-muted',
  cancelled: 'bg-muted/15 text-muted',
  failed: 'bg-danger/15 text-danger',
  dead_letter: 'bg-danger/15 text-danger',
  queued: 'bg-accent/15 text-accent',
  running: 'bg-accent/15 text-accent',
  succeeded: 'bg-ok/15 text-ok',
};

export default function StatusBadge({ status, label, className = '' }) {
  const tone = TONES[status] ?? 'bg-muted/15 text-muted';
  return (
    <span className={`badge ${tone} ${className}`}>{label ?? status}</span>
  );
}
