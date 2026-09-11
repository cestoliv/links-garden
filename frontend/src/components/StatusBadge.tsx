// `set_memberships.status` is one of these four (src/links_garden/db.py's CHECK constraint);
// an unrecognized value falls back to the neutral `pending` styling rather than throwing.
const STATUS_STYLES: Record<string, string> = {
  ok: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  pending: 'border-zinc-700 bg-zinc-800/60 text-zinc-400',
  partial: 'border-amber-400/40 bg-amber-400/10 text-amber-300',
  failed: 'border-red-500/40 bg-red-500/10 text-red-300',
}

const DOT_STYLES: Record<string, string> = {
  ok: 'bg-emerald-400',
  pending: 'bg-zinc-500',
  partial: 'bg-amber-400',
  failed: 'bg-red-400',
}

export function StatusBadge({ status }: { status: string }) {
  const key = status in STATUS_STYLES ? status : 'pending'
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${STATUS_STYLES[key]}`}
    >
      <span aria-hidden="true" className={`size-1.5 rounded-full ${DOT_STYLES[key]}`} />
      {status}
    </span>
  )
}
