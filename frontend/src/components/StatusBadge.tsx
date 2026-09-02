// `set_memberships.status` is one of these four (src/links_garden/db.py's CHECK constraint);
// an unrecognized value falls back to the neutral `pending` styling rather than throwing.
const STATUS_STYLES: Record<string, string> = {
  ok: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-300',
  pending: 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400',
  partial: 'bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-300',
  failed: 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300',
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status] ?? STATUS_STYLES.pending}`}
    >
      {status}
    </span>
  )
}
