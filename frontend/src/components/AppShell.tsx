import { useState } from 'react'
import type { ApiClient } from '../api/client'
import { ComingSoon } from '../pages/ComingSoon'
import { DocumentsPage } from '../pages/DocumentsPage'
import { ReviewPage } from '../pages/ReviewPage'
import { SearchPage } from '../pages/SearchPage'
import { SetAdminPage } from '../pages/SetAdminPage'
import { SetsPage } from '../pages/SetsPage'

type PageId = 'search' | 'documents' | 'sets' | 'review' | 'admin' | 'graph'
type StubPageId = Extract<PageId, 'graph'>

const PAGES: { id: PageId; label: string }[] = [
  { id: 'search', label: 'Search' },
  { id: 'documents', label: 'Documents' },
  { id: 'sets', label: 'Sets' },
  { id: 'review', label: 'Review' },
  { id: 'admin', label: 'Set admin' },
  { id: 'graph', label: 'Graph' },
]

const STUB_COPY: Record<StubPageId, string> = {
  graph: 'A document’s nearest neighbours, two hops out, lands last.',
}

function isStubPage(page: PageId): page is StubPageId {
  return page === 'graph'
}

interface AppShellProps {
  client: ApiClient
  /** A 401 from any request: the token was valid, now isn't. */
  onUnauthorized: () => void
  /** The user's own choice, from the header button: no error to report. */
  onSignOut: () => void
}

export function AppShell({ client, onUnauthorized, onSignOut }: AppShellProps) {
  const [page, setPage] = useState<PageId>('search')

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
        {page === 'search' && <SearchPage client={client} onUnauthorized={onUnauthorized} />}
        {page === 'documents' && <DocumentsPage client={client} onUnauthorized={onUnauthorized} />}
        {page === 'sets' && <SetsPage client={client} onUnauthorized={onUnauthorized} />}
        {page === 'review' && <ReviewPage client={client} onUnauthorized={onUnauthorized} />}
        {page === 'admin' && <SetAdminPage client={client} onUnauthorized={onUnauthorized} />}
        {isStubPage(page) && (
          <ComingSoon title={PAGES.find((item) => item.id === page)?.label ?? ''} description={STUB_COPY[page]} />
        )}
      </main>
    </div>
  )
}
