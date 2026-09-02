import { useEffect, useState } from 'react'
import Markdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { Document, GraphAnchor, Hit } from '../api/types'
import { Button } from '../components/Button'
import { StatusBadge } from '../components/StatusBadge'

interface DocumentPageProps {
  client: ApiClient
  documentId: number
  onOpenDocument: (id: number) => void
  onCenterGraph: (anchor: GraphAnchor) => void
  onBack: () => void
  onUnauthorized: () => void
}

type DocumentState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; document: Document }

type RelatedState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; hits: Hit[] }

// Tailwind ships no prose defaults here (no @tailwindcss/typography), so every element react-
// markdown can produce from this corpus (see DESIGN.md doc-view brief) gets its own styling.
const markdownComponents: Components = {
  h1: ({ children }) => <h1 className="mt-6 text-2xl font-semibold text-zinc-900 dark:text-zinc-50">{children}</h1>,
  h2: ({ children }) => <h2 className="mt-6 text-xl font-semibold text-zinc-900 dark:text-zinc-50">{children}</h2>,
  h3: ({ children }) => <h3 className="mt-5 text-lg font-semibold text-zinc-900 dark:text-zinc-50">{children}</h3>,
  p: ({ children }) => <p className="mt-3 leading-relaxed text-zinc-700 dark:text-zinc-300">{children}</p>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-emerald-700 underline hover:no-underline dark:text-emerald-400">
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="mt-3 list-disc space-y-1 pl-6 text-zinc-700 dark:text-zinc-300">{children}</ul>,
  ol: ({ children }) => <ol className="mt-3 list-decimal space-y-1 pl-6 text-zinc-700 dark:text-zinc-300">{children}</ol>,
  li: ({ children }) => <li>{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="mt-3 border-l-2 border-zinc-300 pl-4 text-zinc-600 italic dark:border-zinc-700 dark:text-zinc-400">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) =>
    /language-/.test(className ?? '') ? (
      <code className={className}>{children}</code>
    ) : (
      <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs dark:bg-zinc-800">{children}</code>
    ),
  pre: ({ children }) => (
    <pre className="mt-3 overflow-x-auto rounded-lg bg-zinc-100 p-3 font-mono text-xs dark:bg-zinc-800">{children}</pre>
  ),
  table: ({ children }) => (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="border-b border-zinc-300 dark:border-zinc-700">{children}</thead>,
  th: ({ children }) => <th className="px-3 py-2 text-left font-medium text-zinc-700 dark:text-zinc-300">{children}</th>,
  td: ({ children }) => (
    <td className="border-t border-zinc-200 px-3 py-2 text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">{children}</td>
  ),
  hr: () => <hr className="mt-6 border-zinc-200 dark:border-zinc-800" />,
}

export function DocumentPage({ client, documentId, onOpenDocument, onCenterGraph, onBack, onUnauthorized }: DocumentPageProps) {
  const [state, setState] = useState<DocumentState>({ status: 'loading' })
  const [related, setRelated] = useState<RelatedState>({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading' })
    client
      .getDocument(documentId)
      .then((document) => {
        if (!cancelled) setState({ status: 'ready', document })
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
  }, [client, documentId, onUnauthorized])

  useEffect(() => {
    let cancelled = false
    setRelated({ status: 'loading' })
    client
      .getRelated(documentId)
      .then((hits) => {
        if (!cancelled) setRelated({ status: 'ready', hits })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setRelated({ status: 'error', message: describeError(error) })
      })
    return () => {
      cancelled = true
    }
  }, [client, documentId, onUnauthorized])

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <Button variant="ghost" size="sm" onClick={onBack}>
        Back
      </Button>
      {state.status === 'loading' && <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">Loading document…</p>}
      {state.status === 'error' && (
        <p role="alert" className="mt-6 text-sm text-red-600 dark:text-red-400">
          {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <DocumentView document={state.document} related={related} onOpenDocument={onOpenDocument} onCenterGraph={onCenterGraph} />
      )}
    </div>
  )
}

function DocumentView({
  document,
  related,
  onOpenDocument,
  onCenterGraph,
}: {
  document: Document
  related: RelatedState
  onOpenDocument: (id: number) => void
  onCenterGraph: (anchor: GraphAnchor) => void
}) {
  return (
    <div className="mt-6">
      <DocumentHeader document={document} onCenterGraph={onCenterGraph} />
      {(document.summary !== null || document.keywords !== null) && <EnrichmentPanel document={document} />}
      <DocumentContent document={document} />
      <RelatedDocuments state={related} onOpenDocument={onOpenDocument} />
    </div>
  )
}

function DocumentHeader({ document, onCenterGraph }: { document: Document; onCenterGraph: (anchor: GraphAnchor) => void }) {
  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">
            {document.title ?? document.url ?? 'Untitled'}
          </h1>
          <p className="mt-0.5 text-xs tracking-wide text-zinc-400 uppercase dark:text-zinc-500">{document.source}</p>
          {document.url !== null && (
            <a
              href={document.url}
              target="_blank"
              rel="noreferrer"
              className="mt-1 block truncate text-sm text-emerald-700 hover:underline dark:text-emerald-400"
            >
              {document.url}
            </a>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="shrink-0"
          onClick={() => {
            onCenterGraph({ id: document.id, title: document.title, url: document.url, embedded: document.embedded })
          }}
        >
          Center the graph on this document
        </Button>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-zinc-500 dark:text-zinc-400">
        <span className="flex items-center gap-1.5">
          Fetched <StatusBadge status={document.status} />
        </span>
        <span className="flex items-center gap-1.5">
          Embedded <StatusBadge status={document.embedded ? 'ok' : 'pending'} />
        </span>
        <span className="flex items-center gap-1.5">
          Enriched <StatusBadge status={document.enriched ? 'ok' : 'pending'} />
        </span>
      </div>
      <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">
        {document.set_names.length > 0 ? `Sets: ${document.set_names.join(', ')}` : 'No sets yet'}
      </p>
    </div>
  )
}

/** Summary and keywords are model output, not the document's own words, so they get a visually
 * distinct panel rather than blending into the content below. */
function EnrichmentPanel({ document }: { document: Document }) {
  return (
    <div className="mt-6 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-4 text-sm dark:border-zinc-700 dark:bg-zinc-900/50">
      <p className="text-xs font-medium tracking-wide text-zinc-500 uppercase dark:text-zinc-400">Generated summary</p>
      {document.summary !== null && <p className="mt-1 text-zinc-700 dark:text-zinc-300">{document.summary}</p>}
      {document.keywords !== null && (
        <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">Keywords: {document.keywords}</p>
      )}
    </div>
  )
}

function DocumentContent({ document }: { document: Document }) {
  return (
    <div className="mx-auto mt-6 max-w-2xl">
      {document.content !== null ? (
        <Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {document.content}
        </Markdown>
      ) : (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {document.error ?? 'This document has no content.'}
        </p>
      )}
    </div>
  )
}

function RelatedDocuments({ state, onOpenDocument }: { state: RelatedState; onOpenDocument: (id: number) => void }) {
  return (
    <div className="mt-10 border-t border-zinc-200 pt-6 dark:border-zinc-800">
      <h2 className="text-xs font-medium tracking-wide text-zinc-500 uppercase dark:text-zinc-400">Related documents</h2>
      {state.status === 'loading' && <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>}
      {state.status === 'error' && (
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          {state.message}
        </p>
      )}
      {state.status === 'ready' && state.hits.length === 0 && (
        <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">No related documents yet.</p>
      )}
      {state.status === 'ready' && state.hits.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1">
          {state.hits.map((hit) => (
            <li key={hit.document_id}>
              <button
                type="button"
                onClick={() => {
                  onOpenDocument(hit.document_id)
                }}
                className="truncate text-left text-sm font-medium text-emerald-700 hover:underline dark:text-emerald-400"
              >
                {hit.title ?? hit.url ?? 'Untitled'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
