// The three stages a document passes through: sync, `garden index`, `garden enrich`. A skipped
// stage is the one thing this page exists to surface, so all three always show, in order, and a
// pending stage stays visibly unlit instead of being dropped.
const DOT_STYLES: Record<'ok' | 'pending' | 'failed', string> = {
  ok: 'bg-emerald-400',
  pending: 'bg-zinc-600',
  failed: 'bg-red-400',
}

const TEXT_STYLES: Record<'ok' | 'pending' | 'failed', string> = {
  ok: 'text-zinc-300',
  pending: 'text-zinc-500',
  failed: 'text-red-300',
}

type Stage = 'ok' | 'pending' | 'failed'

function stageOf(status: string): Stage {
  if (status === 'ok') return 'ok'
  if (status === 'failed') return 'failed'
  return 'pending'
}

interface PipelineStatusProps {
  /** `documents.status`: the fetch result, which may be `failed`. */
  fetched: string
  embedded: boolean
  enriched: boolean
}

export function PipelineStatus({ fetched, embedded, enriched }: PipelineStatusProps) {
  const stages: { label: string; stage: Stage }[] = [
    { label: 'fetch', stage: stageOf(fetched) },
    { label: 'embed', stage: embedded ? 'ok' : 'pending' },
    { label: 'enrich', stage: enriched ? 'ok' : 'pending' },
  ]

  return (
    <span className="flex shrink-0 items-center gap-3 rounded-md border border-zinc-200 px-2 py-1 text-xs dark:border-zinc-800">
      {stages.map(({ label, stage }) => (
        <span key={label} className={`flex items-center gap-1.5 ${TEXT_STYLES[stage]}`}>
          <span aria-hidden="true" className={`size-1.5 rounded-full ${DOT_STYLES[stage]}`} />
          {label}
          <span className="sr-only">: {stage}</span>
        </span>
      ))}
    </span>
  )
}
