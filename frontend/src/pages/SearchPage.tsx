import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { Hit } from '../api/types'
import { useDebouncedValue } from '../hooks/useDebouncedValue'

interface SearchPageProps {
  client: ApiClient
  onUnauthorized: () => void
  onOpenDocument: (id: number) => void
}

type SearchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'results'; hits: Hit[] }

const EASE_OUT = [0.23, 1, 0.32, 1] as const

// See links_garden/search.py: _RRF_K = 60. A document ranked once, at best (rank 1), by only
// one of the two search sides scores exactly 1 / (60 + 1). Anything at or below that ceiling
// never landed a genuine match on both sides, so it's no stronger than a single, best-case,
// one-sided hit — the signal used below to warn about a weak top result.
const RRF_SINGLE_SIDE_CEILING = 1 / 61

function hasWeakTopMatch(hits: Hit[]): boolean {
  return hits.length > 0 && hits[0].score <= RRF_SINGLE_SIDE_CEILING
}

export function SearchPage({ client, onUnauthorized, onOpenDocument }: SearchPageProps) {
  const [query, setQuery] = useState('')
  const debouncedQuery = useDebouncedValue(query, 400)
  const [state, setState] = useState<SearchState>({ status: 'idle' })
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const reduceMotion = useReducedMotion()

  useEffect(() => {
    const trimmed = debouncedQuery.trim()
    if (trimmed === '') {
      setState({ status: 'idle' })
      return
    }
    let cancelled = false
    setState({ status: 'loading' })
    client
      .search(trimmed, 20)
      .then((hits) => {
        if (!cancelled) setState({ status: 'results', hits })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setState({ status: 'error', message: describeError(error) })
      })
    return () => {
      cancelled = true
    }
  }, [debouncedQuery, client, onUnauthorized])

  // Embedding the query runs a local model and can take many seconds (measured up to ~30s on
  // the real garden); a ticking counter tells the user the app is still working, not frozen.
  useEffect(() => {
    if (state.status !== 'loading') return
    const start = Date.now()
    setElapsedSeconds(0)
    const id = setInterval(() => {
      setElapsedSeconds((Date.now() - start) / 1000)
    }, 200)
    return () => {
      clearInterval(id)
    }
  }, [state.status])

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Search</h1>
      <input
        type="search"
        id="search-query"
        name="search-query"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value)
        }}
        placeholder="Search your garden…"
        aria-label="Search"
        className="mt-4 w-full rounded-md border border-zinc-300 bg-white px-4 py-2.5 text-sm text-zinc-900 outline-none focus:border-emerald-600 focus:ring-2 focus:ring-emerald-600/30 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      />
      <div className="mt-6">
        <SearchResults
          state={state}
          query={debouncedQuery.trim()}
          elapsedSeconds={elapsedSeconds}
          reduceMotion={reduceMotion}
          onOpenDocument={onOpenDocument}
        />
      </div>
    </div>
  )
}

function SearchResults({
  state,
  query,
  elapsedSeconds,
  reduceMotion,
  onOpenDocument,
}: {
  state: SearchState
  query: string
  elapsedSeconds: number
  reduceMotion: boolean | null
  onOpenDocument: (id: number) => void
}) {
  const fade = { initial: reduceMotion ? false : { opacity: 0 }, exit: { opacity: 0 } } as const

  return (
    <AnimatePresence mode="wait">
      {state.status === 'idle' && (
        <motion.p
          key="idle"
          {...fade}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.15 }}
          className="text-sm text-zinc-500 dark:text-zinc-400"
        >
          Type to search titles, summaries and your own notes.
        </motion.p>
      )}
      {state.status === 'loading' && (
        <motion.div
          key="loading"
          {...fade}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.15 }}
          className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400"
        >
          <span
            aria-hidden="true"
            className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
          />
          <span role="status">
            Searching… {elapsedSeconds.toFixed(1)}s. Embeddings run locally and can take a while.
          </span>
        </motion.div>
      )}
      {state.status === 'error' && (
        <motion.p
          key="error"
          role="alert"
          {...fade}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.15 }}
          className="text-sm text-red-600 dark:text-red-400"
        >
          {state.message}
        </motion.p>
      )}
      {state.status === 'results' && state.hits.length === 0 && (
        <motion.p
          key="empty"
          {...fade}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.15 }}
          className="text-sm text-zinc-500 dark:text-zinc-400"
        >
          No results for “{query}”.
        </motion.p>
      )}
      {state.status === 'results' && state.hits.length > 0 && (
        <ResultsList hits={state.hits} query={query} fade={fade} reduceMotion={reduceMotion} onOpenDocument={onOpenDocument} />
      )}
    </AnimatePresence>
  )
}

function ResultsList({
  hits,
  query,
  fade,
  reduceMotion,
  onOpenDocument,
}: {
  hits: Hit[]
  query: string
  fade: { initial: false | { opacity: number }; exit: { opacity: number } }
  reduceMotion: boolean | null
  onOpenDocument: (id: number) => void
}) {
  return (
    <motion.div key="results" {...fade} animate={{ opacity: 1 }} transition={{ duration: 0.15 }}>
      {hasWeakTopMatch(hits) && (
        <p className="mb-3 text-sm text-zinc-500 dark:text-zinc-400">
          No strong match for “{query}”. Showing the closest results below.
        </p>
      )}
      <ul className="flex flex-col gap-3">
        {hits.map((hit, index) => (
          <ResultCard key={hit.document_id} hit={hit} index={index} reduceMotion={reduceMotion} onOpenDocument={onOpenDocument} />
        ))}
      </ul>
    </motion.div>
  )
}

function ResultCard({
  hit,
  index,
  reduceMotion,
  onOpenDocument,
}: {
  hit: Hit
  index: number
  reduceMotion: boolean | null
  onOpenDocument: (id: number) => void
}) {
  return (
    <motion.li
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, delay: Math.min(index, 6) * 0.04, ease: EASE_OUT }}
      className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <button
            type="button"
            onClick={() => {
              onOpenDocument(hit.document_id)
            }}
            className="truncate text-left text-sm font-medium text-emerald-700 hover:underline dark:text-emerald-400"
          >
            {hit.title ?? hit.url ?? 'Untitled'}
          </button>
          <p className="mt-0.5 text-xs tracking-wide text-zinc-400 uppercase dark:text-zinc-500">
            {hit.source}
          </p>
        </div>
        <span className="shrink-0 rounded bg-zinc-100 px-2 py-0.5 font-mono text-xs text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
          {hit.score.toFixed(3)}
        </span>
      </div>
      <p className="mt-2 line-clamp-4 text-sm break-words whitespace-pre-line text-zinc-600 dark:text-zinc-300">
        {hit.snippet}
      </p>
    </motion.li>
  )
}
