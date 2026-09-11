import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { DocumentListItem, GraphAnchor } from '../api/types'
import { buttonClassName } from '../components/buttonClassName'
import { Link } from '../components/Link'
import { PageHeader } from '../components/PageHeader'
import { PipelineStatus } from '../components/PipelineStatus'

interface DocumentsPageProps {
  client: ApiClient
  onUnauthorized: () => void
  onOpenGraph: (anchor: GraphAnchor) => void
  onOpenDocument: (id: number) => void
}

// Matches the API's own default page size (src/links_garden/api.py's _DOCUMENTS_DEFAULT_LIMIT).
const PAGE_LIMIT = 50

type DocumentsState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | {
      status: 'ready'
      items: DocumentListItem[]
      done: boolean
      loadingMore: boolean
      loadMoreError: string | null
    }

export function DocumentsPage({ client, onUnauthorized, onOpenGraph, onOpenDocument }: DocumentsPageProps) {
  const [state, setState] = useState<DocumentsState>({ status: 'loading' })
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  // Refs, not state: the fetch itself must see "a request is already running" and "there is no
  // next page" synchronously, before React commits any re-render, or a sentinel that fires
  // twice in the same tick would start two requests.
  const cursorRef = useRef<string | null>(null)
  const fetchingRef = useRef(false)
  const doneRef = useRef(false)

  const fetchPage = useCallback(() => {
    if (fetchingRef.current || doneRef.current) return
    fetchingRef.current = true
    setState((prev) => (prev.status === 'ready' ? { ...prev, loadingMore: true, loadMoreError: null } : prev))
    client
      .listDocuments({ limit: PAGE_LIMIT, cursor: cursorRef.current ?? undefined })
      .then((page) => {
        cursorRef.current = page.next_cursor
        doneRef.current = page.next_cursor === null
        setState((prev) => ({
          status: 'ready',
          items: prev.status === 'ready' ? [...prev.items, ...page.items] : page.items,
          done: doneRef.current,
          loadingMore: false,
          loadMoreError: null,
        }))
      })
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        const message = describeError(error)
        setState((prev) =>
          prev.status === 'ready' ? { ...prev, loadingMore: false, loadMoreError: message } : { status: 'error', message },
        )
      })
      .finally(() => {
        fetchingRef.current = false
      })
  }, [client, onUnauthorized])

  useEffect(() => {
    fetchPage()
  }, [fetchPage])

  const isReady = state.status === 'ready'
  const done = isReady && state.done

  // Recreated only when readiness or done-ness changes, i.e. once per page rather than once per
  // scroll event — so it stays attached across pages and is torn down for good once done flips.
  useEffect(() => {
    if (!isReady || done) return
    const node = sentinelRef.current
    if (node === null) return
    const observer = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting) fetchPage()
    })
    observer.observe(node)
    return () => {
      observer.disconnect()
    }
  }, [isReady, done, fetchPage])

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <PageHeader
        eyebrow="index"
        title="Documents"
        description="Every document in the garden, newest first, with its index status."
        actions={
          state.status === 'ready' ? (
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              {state.items.length} loaded{state.done ? '' : ' +'}
            </span>
          ) : undefined
        }
      />
      <DocumentsList
        state={state}
        done={done}
        sentinelRef={sentinelRef}
        onOpenGraph={onOpenGraph}
        onOpenDocument={onOpenDocument}
      />
    </div>
  )
}

function DocumentsList({
  state,
  done,
  sentinelRef,
  onOpenGraph,
  onOpenDocument,
}: {
  state: DocumentsState
  done: boolean
  sentinelRef: RefObject<HTMLDivElement | null>
  onOpenGraph: (anchor: GraphAnchor) => void
  onOpenDocument: (id: number) => void
}) {
  if (state.status === 'loading') {
    return <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">Loading documents…</p>
  }
  if (state.status === 'error') {
    return (
      <p role="alert" className="mt-6 text-sm text-red-600 dark:text-red-400">
        {state.message}
      </p>
    )
  }
  if (state.items.length === 0) {
    return <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">No documents yet.</p>
  }
  return (
    <>
      <ul className="panel mt-6 divide-y divide-zinc-200 dark:divide-zinc-800">
        {state.items.map((item) => (
          <DocumentRow key={item.id} item={item} onOpenGraph={onOpenGraph} onOpenDocument={onOpenDocument} />
        ))}
      </ul>
      {!done && (
        <div ref={sentinelRef} className="py-4 text-center text-sm text-zinc-500 dark:text-zinc-400">
          {state.loadingMore && 'Loading more…'}
        </div>
      )}
      {state.loadMoreError !== null && (
        <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">
          {state.loadMoreError}
        </p>
      )}
    </>
  )
}

function DocumentRow({
  item,
  onOpenGraph,
  onOpenDocument,
}: {
  item: DocumentListItem
  onOpenGraph: (anchor: GraphAnchor) => void
  onOpenDocument: (id: number) => void
}) {
  return (
    <li className="px-4 py-3 transition-colors duration-150 hover:bg-zinc-100/60 dark:hover:bg-zinc-800/40">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="min-w-0">
          <Link
            href={`/documents/${String(item.id)}`}
            onNavigate={() => {
              onOpenDocument(item.id)
            }}
            className="block truncate text-left font-medium text-emerald-700 hover:underline dark:text-emerald-400"
          >
            {item.title ?? item.url ?? 'Untitled'}
          </Link>
          <p className="mt-1.5 truncate text-xs text-zinc-500 dark:text-zinc-400">
            {item.source}
            <span aria-hidden="true" className="px-2 text-zinc-700">/</span>
            <span className="normal-case tracking-normal">
              {item.set_names.length > 0 ? item.set_names.join(', ') : 'no sets yet'}
            </span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <PipelineStatus fetched={item.status} embedded={item.embedded} enriched={item.enriched} />
          <Link
          href={`/graph/${String(item.id)}`}
          onNavigate={() => {
            onOpenGraph({ id: item.id, title: item.title, url: item.url, embedded: item.embedded })
          }}
          className={`shrink-0 ${buttonClassName({ variant: 'ghost', size: 'sm' })}`}
          >
            Graph
          </Link>
        </div>
      </div>
      {item.error !== null && <p className="mt-1.5 text-sm text-red-600 dark:text-red-400">{item.error}</p>}
    </li>
  )
}
