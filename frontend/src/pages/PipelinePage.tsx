import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type {
  Pipeline,
  PipelineItem,
  PipelineStage,
  PipelineStageName,
  StageRun,
} from '../api/types'
import { Button } from '../components/Button'
import { ConnectionsPanel } from '../components/ConnectionsPanel'
import { Link } from '../components/Link'
import { PageHeader } from '../components/PageHeader'

interface PipelinePageProps {
  client: ApiClient
  onUnauthorized: () => void
  onOpenDocument: (id: number) => void
}

type PipelineState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; pipeline: Pipeline }

// Ingestion runs from cron as well as from this page, so the numbers move on their own. Ten
// seconds is slow enough to stay invisible on the API, and a run in flight polls faster so the
// queue visibly drains while you watch it.
const IDLE_POLL_MS = 10_000
const RUNNING_POLL_MS = 3_000

const STAGE_COPY: Record<PipelineStageName, { label: string; blurb: string }> = {
  fetch: {
    label: 'Fetch',
    blurb: 'Sources read into the store. A retry re-fetches the queue and spends fetch credits.',
  },
  embed: { label: 'Embed', blurb: 'Chunks vectorised for search. Needs ollama.' },
  enrich: { label: 'Enrich', blurb: 'Summary, keywords and set matches written. Needs ollama.' },
  extract: { label: 'Extract', blurb: 'Set schemas filled per membership. Needs ollama.' },
}

export function PipelinePage({ client, onUnauthorized, onOpenDocument }: PipelinePageProps) {
  const [state, setState] = useState<PipelineState>({ status: 'loading' })
  const [retryError, setRetryError] = useState<string | null>(null)

  const handleError = useCallback(
    (error: unknown, report: (message: string) => void) => {
      if (isUnauthorized(error)) {
        onUnauthorized()
        return
      }
      report(describeError(error))
    },
    [onUnauthorized],
  )

  const load = useCallback(() => {
    client
      .getPipeline()
      .then((pipeline) => {
        setState({ status: 'ready', pipeline })
      })
      .catch((error: unknown) => {
        handleError(error, (message) => {
          setState({ status: 'error', message })
        })
      })
  }, [client, handleError])

  const run = state.status === 'ready' ? state.pipeline.run : null
  const running = run !== null && run.state === 'running'

  const retry = useCallback(
    (stage: PipelineStageName) => {
      setRetryError(null)
      client
        .retryStage(stage)
        .then(load)
        .catch((error: unknown) => {
          handleError(error, setRetryError)
        })
    },
    [client, handleError, load],
  )

  useEffect(() => {
    load()
    const timer = setInterval(load, running ? RUNNING_POLL_MS : IDLE_POLL_MS)
    return () => {
      clearInterval(timer)
    }
  }, [load, running])

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <PageHeader
        eyebrow="ingestion"
        title="Pipeline"
        description="Where the corpus stands at each stage, and which documents are still queued for it."
        actions={
          <Button variant="ghost" size="sm" onClick={load}>
            Refresh
          </Button>
        }
      />
      {retryError !== null && (
        <p role="alert" className="mt-4 text-sm text-red-600 dark:text-red-400">
          {retryError}
        </p>
      )}
      <PipelineBody
        state={state}
        running={running}
        onRetry={retry}
        onOpenDocument={onOpenDocument}
      />
      <ConnectionsPanel client={client} onUnauthorized={onUnauthorized} />
    </div>
  )
}

interface BodyProps {
  state: PipelineState
  running: boolean
  onRetry: (stage: PipelineStageName) => void
  onOpenDocument: (id: number) => void
}

function PipelineBody({ state, running, onRetry, onOpenDocument }: BodyProps) {
  if (state.status === 'loading') {
    return <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">Reading the pipeline…</p>
  }
  if (state.status === 'error') {
    return (
      <p role="alert" className="mt-6 text-sm text-red-600 dark:text-red-400">
        {state.message}
      </p>
    )
  }
  const run = state.pipeline.run
  return (
    <div className="mt-6 flex flex-col gap-4">
      {state.pipeline.stages.map((stage) => (
        <StageSection
          key={stage.stage}
          stage={stage}
          run={run !== null && run.stage === stage.stage ? run : null}
          running={running}
          onRetry={onRetry}
          onOpenDocument={onOpenDocument}
        />
      ))}
    </div>
  )
}

interface StageSectionProps {
  stage: PipelineStage
  /** The run that belongs to this stage, if the latest one does. */
  run: StageRun | null
  /** True while any stage is running: the API runs one at a time, so every button waits. */
  running: boolean
  onRetry: (stage: PipelineStageName) => void
  onOpenDocument: (id: number) => void
}

function StageSection({ stage, run, running, onRetry, onOpenDocument }: StageSectionProps) {
  const copy = STAGE_COPY[stage.stage]
  const total = stage.done + stage.waiting + stage.failed
  const queued = stage.waiting + stage.failed
  const hidden = queued - stage.items.length

  return (
    <section className="panel p-4" aria-labelledby={`stage-${stage.stage}`}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="min-w-0">
          <h2
            id={`stage-${stage.stage}`}
            className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
          >
            {copy.label}
          </h2>
          <p className="mt-0.5 text-sm text-zinc-500 dark:text-zinc-400">{copy.blurb}</p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            onRetry(stage.stage)
          }}
          disabled={running}
          title={running ? 'Another stage is running. Stages run one at a time.' : undefined}
        >
          {run !== null && run.state === 'running' ? 'Running…' : 'Run stage'}
        </Button>
      </div>
      <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <Count value={stage.done} label="done" tone="text-zinc-700 dark:text-zinc-200" />
        <Count value={stage.waiting} label="waiting" tone="text-zinc-700 dark:text-zinc-200" />
        <Count value={stage.failed} label="failed" tone="text-red-600 dark:text-red-400" />
      </p>
      <ProgressRail label={copy.label} done={stage.done} failed={stage.failed} total={total} />
      {run !== null && <RunNote run={run} />}
      {queued === 0 ? (
        <p className="mt-3 text-sm text-zinc-500 dark:text-zinc-400">
          {total === 0
            ? 'Nothing eligible for this stage yet.'
            : 'Queue empty. Every eligible document is through.'}
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-zinc-200 dark:divide-zinc-800">
          {stage.items.map((item) => (
            <QueueRow
              key={`${String(item.document_id)}:${item.detail ?? ''}`}
              item={item}
              onOpenDocument={onOpenDocument}
            />
          ))}
        </ul>
      )}
      {hidden > 0 && (
        <p className="mt-3 text-xs text-zinc-500 dark:text-zinc-400">+{hidden} more queued</p>
      )}
    </section>
  )
}

// The stage runs on a background thread, so its outcome can only arrive with a later poll.
// This is where it lands: the same place as the counts it just changed.
function RunNote({ run }: { run: StageRun }) {
  if (run.state === 'running') {
    return (
      <p className="mt-3 text-sm text-zinc-500 dark:text-zinc-400">
        Running now. The counts update as it goes.
      </p>
    )
  }
  const failed = run.state === 'error'
  return (
    <p
      role="status"
      className={`mt-3 text-sm ${failed ? 'text-red-600 dark:text-red-400' : 'text-zinc-500 dark:text-zinc-400'}`}
    >
      {failed ? 'Last run failed: ' : 'Last run: '}
      {run.detail ?? 'no detail reported'}
    </p>
  )
}

function Count({ value, label, tone }: { value: number; label: string; tone: string }) {
  return (
    <span className={value === 0 ? 'text-zinc-500' : tone}>
      <span className="font-semibold">{value}</span>{' '}
      <span className="text-zinc-500 dark:text-zinc-400">{label}</span>
    </span>
  )
}

// A single rail per stage rather than a number alone: the share already through is the one thing
// a glance should answer. Failures sit at the far end in red so a stuck stage reads as stuck.
function ProgressRail({
  label,
  done,
  failed,
  total,
}: {
  label: string
  done: number
  failed: number
  total: number
}) {
  const percent = total === 0 ? 0 : Math.round((done / total) * 100)
  const failedPercent = total === 0 ? 0 : (failed / total) * 100

  return (
    <div
      role="progressbar"
      aria-label={`${label} progress`}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      aria-valuetext={`${String(percent)}% of ${String(total)} done`}
      className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
    >
      <span className="bg-emerald-500" style={{ width: `${String(percent)}%` }} />
      <span className="bg-red-400" style={{ width: `${String(failedPercent)}%` }} />
    </div>
  )
}

function QueueRow({
  item,
  onOpenDocument,
}: {
  item: PipelineItem
  onOpenDocument: (id: number) => void
}) {
  return (
    <li className="flex items-start justify-between gap-3 py-2">
      <div className="flex min-w-0 items-start gap-2">
        <span
          aria-hidden="true"
          className={`mt-1.5 size-1.5 shrink-0 rounded-full ${
            item.state === 'failed' ? 'bg-red-400' : 'bg-zinc-500'
          }`}
        />
        <div className="min-w-0">
          <Link
            href={`/documents/${String(item.document_id)}`}
            onNavigate={() => {
              onOpenDocument(item.document_id)
            }}
            className="block truncate text-sm font-medium text-emerald-700 hover:underline dark:text-emerald-400"
          >
            {item.title ?? item.url ?? 'Untitled'}
          </Link>
          {item.detail !== null && (
            <p
              className={`mt-0.5 truncate text-xs ${item.state === 'failed' ? 'text-red-600 dark:text-red-400' : 'text-zinc-500'}`}
            >
              {item.detail}
            </p>
          )}
        </div>
      </div>
      <span className="shrink-0 text-xs whitespace-nowrap text-zinc-500">
        {item.source}
        <span className="sr-only">, {item.state}</span>
      </span>
    </li>
  )
}
