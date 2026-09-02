import { useState } from 'react'
import type { ApiClient } from '../api/client'
import type { GraphAnchor } from '../api/types'
import { DocumentPage } from '../pages/DocumentPage'
import { DocumentsPage } from '../pages/DocumentsPage'
import { GraphPage } from '../pages/GraphPage'
import { ReviewPage } from '../pages/ReviewPage'
import { SearchPage } from '../pages/SearchPage'
import { SetAdminPage } from '../pages/SetAdminPage'
import { SetsPage } from '../pages/SetsPage'

type PageId = 'search' | 'documents' | 'sets' | 'review' | 'admin' | 'graph' | 'document'

const PAGES: { id: PageId; label: string }[] = [
  { id: 'search', label: 'Search' },
  { id: 'documents', label: 'Documents' },
  { id: 'sets', label: 'Sets' },
  { id: 'review', label: 'Review' },
  { id: 'admin', label: 'Set admin' },
  { id: 'graph', label: 'Graph' },
]

interface AppShellProps {
  client: ApiClient
  /** A 401 from any request: the token was valid, now isn't. */
  onUnauthorized: () => void
  /** The user's own choice, from the header button: no error to report. */
  onSignOut: () => void
}

export function AppShell({ client, onUnauthorized, onSignOut }: AppShellProps) {
  const [page, setPage] = useState<PageId>('search')
  // Lives here, alongside the page id, so a control on another page (Documents) can root the
  // graph on a document and switch to it in one click. No Context, no router: this is the whole
  // amount of cross-page state the dashboard needs.
  const [graphAnchor, setGraphAnchor] = useState<GraphAnchor | null>(null)
  // The document view's own state: which document is open, and which page to return to. Back
  // always lands on the page that was current the last time the view was entered from outside
  // itself, so following a neighbour link from inside the view never overwrites it.
  const [documentId, setDocumentId] = useState<number | null>(null)
  const [documentReturnPage, setDocumentReturnPage] = useState<PageId>('search')

  function openGraph(anchor: GraphAnchor) {
    setGraphAnchor(anchor)
    setPage('graph')
  }

  function openDocument(id: number) {
    if (page !== 'document') setDocumentReturnPage(page)
    setDocumentId(id)
    setPage('document')
  }

  return (
    <div className="min-h-dvh">
      <header className="flex h-14 items-center justify-between border-b border-zinc-200 px-6 dark:border-zinc-800">
        <div className="flex items-center gap-6">
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
            Links Garden
          </span>
          <nav className="flex gap-1">
            {PAGES.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => {
                  setPage(item.id)
                }}
                aria-current={page === item.id ? 'page' : undefined}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors duration-150 ${
                  page === item.id
                    ? 'bg-emerald-700 text-white dark:bg-emerald-600'
                    : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800'
                }`}
              >
                {item.label}
              </button>
            ))}
          </nav>
        </div>
        <button
          type="button"
          onClick={onSignOut}
          className="text-sm text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
        >
          Sign out
        </button>
      </header>
      <main>
        {page === 'search' && (
          <SearchPage client={client} onUnauthorized={onUnauthorized} onOpenDocument={openDocument} />
        )}
        {page === 'documents' && (
          <DocumentsPage
            client={client}
            onUnauthorized={onUnauthorized}
            onOpenGraph={openGraph}
            onOpenDocument={openDocument}
          />
        )}
        {page === 'sets' && <SetsPage client={client} onUnauthorized={onUnauthorized} />}
        {page === 'review' && <ReviewPage client={client} onUnauthorized={onUnauthorized} />}
        {page === 'admin' && <SetAdminPage client={client} onUnauthorized={onUnauthorized} />}
        {page === 'graph' && (
          <GraphPage
            client={client}
            anchor={graphAnchor}
            onAnchorChange={setGraphAnchor}
            onOpenDocument={openDocument}
            onUnauthorized={onUnauthorized}
          />
        )}
        {page === 'document' && documentId !== null && (
          <DocumentPage
            client={client}
            documentId={documentId}
            onOpenDocument={openDocument}
            onCenterGraph={openGraph}
            onBack={() => {
              setPage(documentReturnPage)
            }}
            onUnauthorized={onUnauthorized}
          />
        )}
      </main>
    </div>
  )
}
