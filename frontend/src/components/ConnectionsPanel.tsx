import { useCallback, useState } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { Connection } from '../api/types'
import { Button } from './Button'

interface ConnectionsPanelProps {
  client: ApiClient
  onUnauthorized: () => void
}

// Both pings go out over the network on click only, never on load or on the pipeline poll:
// Firecrawl is a third party, and a ping nobody asked for is a request nobody reads.
export function ConnectionsPanel({ client, onUnauthorized }: ConnectionsPanelProps) {
  return (
    <section className="panel mt-4 p-4" aria-labelledby="connections">
      <h2 id="connections" className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
        Connections
      </h2>
      <p className="mt-0.5 text-sm text-zinc-500 dark:text-zinc-400">
        The two services ingestion depends on. Checked when you ask, not in the background.
      </p>
      <div className="mt-4 flex flex-col gap-4">
        <ConnectionRow
          label="Firecrawl"
          hint="Fetch backend. The ping reads the credit balance and spends nothing."
          ping={client.pingFirecrawl}
          onUnauthorized={onUnauthorized}
        />
        <div>
          <ConnectionRow
            label="ollama"
            hint="Embeddings and enrichment. The ping only checks that both models are pulled."
            ping={client.pingOllama}
            onUnauthorized={onUnauthorized}
          />
          <OllamaGeneration client={client} onUnauthorized={onUnauthorized} />
        </div>
      </div>
    </section>
  )
}

type CheckState =
  | { status: 'idle' }
  | { status: 'checking' }
  | { status: 'done'; connection: Connection }
  | { status: 'error'; message: string }

interface ConnectionRowProps {
  label: string
  hint: string
  ping: () => Promise<Connection>
  onUnauthorized: () => void
}

function ConnectionRow({ label, hint, ping, onUnauthorized }: ConnectionRowProps) {
  const [state, setState] = useState<CheckState>({ status: 'idle' })

  const check = useCallback(() => {
    setState({ status: 'checking' })
    ping()
      .then((connection) => {
        setState({ status: 'done', connection })
      })
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setState({ status: 'error', message: describeError(error) })
      })
  }, [ping, onUnauthorized])

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-sm font-medium text-zinc-800 dark:text-zinc-100">
            <StatusDot state={state} />
            {label}
          </p>
          <p className="mt-0.5 text-sm text-zinc-500 dark:text-zinc-400">{hint}</p>
        </div>
        <Button variant="ghost" size="sm" onClick={check} disabled={state.status === 'checking'}>
          {state.status === 'checking' ? 'Pinging…' : 'Ping'}
        </Button>
      </div>
      <CheckNote label={label} state={state} />
    </div>
  )
}

// A ping's answer is the whole point, so it lands as text rather than as a colour alone: the dot
// repeats what the sentence already says.
function CheckNote({ label, state }: { label: string; state: CheckState }) {
  if (state.status === 'idle') {
    return <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">Not checked yet.</p>
  }
  if (state.status === 'checking') {
    return <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">Checking {label}…</p>
  }
  if (state.status === 'error') {
    return (
      <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
        {state.message}
      </p>
    )
  }
  return (
    <p
      role="status"
      className={`mt-2 text-sm ${
        state.connection.ok ? 'text-zinc-600 dark:text-zinc-300' : 'text-red-600 dark:text-red-400'
      }`}
    >
      {state.connection.ok ? 'Reachable: ' : 'Problem: '}
      {state.connection.detail}
    </p>
  )
}

function StatusDot({ state }: { state: CheckState }) {
  const tone =
    state.status === 'done'
      ? state.connection.ok
        ? 'bg-emerald-400'
        : 'bg-red-400'
      : state.status === 'error'
        ? 'bg-red-400'
        : 'bg-zinc-600'
  return <span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${tone}`} />
}

type GenerationState =
  | { status: 'idle' }
  | { status: 'sending' }
  | { status: 'done'; reply: string; elapsedMs: number }
  | { status: 'error'; message: string }

const DEFAULT_PROMPT = 'Say hello in three words.'

// A pulled model proves nothing: ollama lists tags from disk, and a model that cannot load only
// fails once something asks it for tokens. This is that ask.
function OllamaGeneration({ client, onUnauthorized }: ConnectionsPanelProps) {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT)
  const [state, setState] = useState<GenerationState>({ status: 'idle' })

  function send(event: { preventDefault: () => void }) {
    event.preventDefault()
    setState({ status: 'sending' })
    client
      .generateWithOllama(prompt)
      .then((generation) => {
        setState({ status: 'done', reply: generation.reply, elapsedMs: generation.elapsed_ms })
      })
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setState({ status: 'error', message: describeError(error) })
      })
  }

  return (
    <form onSubmit={send} className="mt-3">
      <label
        htmlFor="ollama-prompt"
        className="text-sm font-medium text-zinc-700 dark:text-zinc-200"
      >
        Test prompt
      </label>
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <input
          id="ollama-prompt"
          name="ollama-prompt"
          value={prompt}
          maxLength={2000}
          disabled={state.status === 'sending'}
          onChange={(event) => {
            setPrompt(event.target.value)
          }}
          className="min-w-0 flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none focus:border-emerald-500 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
        />
        <Button type="submit" size="sm" disabled={state.status === 'sending' || prompt.trim() === ''}>
          {state.status === 'sending' ? 'Generating…' : 'Send'}
        </Button>
      </div>
      <GenerationNote state={state} />
    </form>
  )
}

function GenerationNote({ state }: { state: GenerationState }) {
  if (state.status === 'idle') {
    return (
      <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
        Send a prompt to check that the model answers. The first call after a restart is slow,
        because ollama loads the model.
      </p>
    )
  }
  if (state.status === 'sending') {
    return (
      <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
        Generating. This can take a minute.
      </p>
    )
  }
  if (state.status === 'error') {
    return (
      <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
        {state.message}
      </p>
    )
  }
  return (
    <div role="status" className="mt-2">
      <p className="text-xs text-zinc-500 dark:text-zinc-400">
        Answered in {(state.elapsedMs / 1000).toFixed(1)}s
      </p>
      <p className="mt-1 text-sm whitespace-pre-wrap text-zinc-700 dark:text-zinc-200">
        {state.reply}
      </p>
    </div>
  )
}
