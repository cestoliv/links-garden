import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { GraphAnchor } from '../api/types'
import type { Route } from '../hooks/useRouter'
import { useRouter } from '../hooks/useRouter'
import { DocumentPage } from '../pages/DocumentPage'
import { DocumentsPage } from '../pages/DocumentsPage'
import { GraphPage } from '../pages/GraphPage'
import { ReviewPage } from '../pages/ReviewPage'
import { SearchPage } from '../pages/SearchPage'
import { SetAdminPage } from '../pages/SetAdminPage'
import { SetsPage } from '../pages/SetsPage'
import { Link } from './Link'

const NAV_ITEMS: { path: string; label: string; matches: Route['name'] }[] = [
  { path: '/', label: 'Search', matches: 'search' },
  { path: '/documents', label: 'Documents', matches: 'documents' },
  { path: '/sets', label: 'Sets', matches: 'sets' },
  { path: '/review', label: 'Review', matches: 'review' },
  { path: '/admin', label: 'Set admin', matches: 'admin' },
  { path: '/graph', label: 'Graph', matches: 'graph' },
]

interface AppShellProps {
  client: ApiClient
  /** A 401 from any request: the token was valid, now isn't. */
  onUnauthorized: () => void
  /** The user's own choice, from the header button: no error to report. */
  onSignOut: () => void
}

export function AppShell({ client, onUnauthorized, onSignOut }: AppShellProps) {
  const { route, navigate } = useRouter()
  // The graph view's anchor id lives in the URL (Decision 4): this resolves it to the
  // title/url/embedded GraphPage needs, so a typed-in /graph/42 works exactly like clicking
  // there from inside the app, and GraphPage itself never has to know about routing.
  const [graphAnchor, setGraphAnchor] = useState<GraphAnchor | null>(null)
  const [graphAnchorError, setGraphAnchorError] = useState<string | null>(null)
  const anchorId = route.name === 'graph' ? route.anchorId : null

  useEffect(() => {
    if (anchorId === null) {
      setGraphAnchor(null)
      setGraphAnchorError(null)
      return
    }
    let cancelled = false
    setGraphAnchorError(null)
    client
      .getDocument(anchorId)
      .then((document) => {
        if (cancelled) return
        setGraphAnchor({ id: document.id, title: document.title, url: document.url, embedded: document.embedded })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setGraphAnchorError(describeError(error))
      })
    return () => {
      cancelled = true
    }
  }, [anchorId, client, onUnauthorized])

  const openDocument = useCallback(
    (id: number) => {
      navigate(`/documents/${String(id)}`)
    },
    [navigate],
  )

  const openGraph = useCallback(
    (anchor: GraphAnchor) => {
      navigate(`/graph/${String(anchor.id)}`)
    },
    [navigate],
  )

  const selectSet = useCallback(
    (name: string) => {
      navigate(`/sets/${encodeURIComponent(name)}`)
    },
    [navigate],
  )

  // Replaces the current entry instead of pushing: a fresh entry per keystroke would make Back
  // step through the search box one character at a time instead of leaving the page.
  const changeQuery = useCallback(
    (query: string) => {
      navigate(query.trim() === '' ? '/' : `/?q=${encodeURIComponent(query)}`, { replace: true })
    },
    [navigate],
  )

  const goBack = useCallback(() => {
    window.history.back()
  }, [])

  return (
    <div className="min-h-dvh">
      <header className="flex h-14 items-center justify-between border-b border-zinc-200 px-6 dark:border-zinc-800">
        <div className="flex items-center gap-6">
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
            Links Garden
          </span>
          <nav className="flex gap-1">
            {NAV_ITEMS.map((item) => (
              <Link
                key={item.path}
                href={item.path}
                onNavigate={() => {
                  navigate(item.path)
                }}
                aria-current={route.name === item.matches ? 'page' : undefined}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors duration-150 ${
                  route.name === item.matches
                    ? 'bg-emerald-700 text-white dark:bg-emerald-600'
                    : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800'
                }`}
              >
                {item.label}
              </Link>
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
        <RouteContent
          route={route}
          client={client}
          onUnauthorized={onUnauthorized}
          openDocument={openDocument}
          openGraph={openGraph}
          selectSet={selectSet}
          changeQuery={changeQuery}
          goBack={goBack}
          navigate={navigate}
          graphAnchor={graphAnchor}
          graphAnchorError={graphAnchorError}
        />
      </main>
    </div>
  )
}

interface RouteContentProps {
  route: Route
  client: ApiClient
  onUnauthorized: () => void
  openDocument: (id: number) => void
  openGraph: (anchor: GraphAnchor) => void
  selectSet: (name: string) => void
  changeQuery: (query: string) => void
  goBack: () => void
  navigate: (path: string) => void
  graphAnchor: GraphAnchor | null
  graphAnchorError: string | null
}

// Split out of AppShell so its cyclomatic complexity (one branch per route, plus the graph
// view's error/ready split) is counted on its own instead of piling onto AppShell's.
function RouteContent({
  route,
  client,
  onUnauthorized,
  openDocument,
  openGraph,
  selectSet,
  changeQuery,
  goBack,
  navigate,
  graphAnchor,
  graphAnchorError,
}: RouteContentProps) {
  if (route.name === 'search') {
    return (
      <SearchPage
        client={client}
        onUnauthorized={onUnauthorized}
        onOpenDocument={openDocument}
        initialQuery={route.query}
        onQueryChange={changeQuery}
      />
    )
  }
  if (route.name === 'documents') {
    return <DocumentsPage client={client} onUnauthorized={onUnauthorized} onOpenGraph={openGraph} onOpenDocument={openDocument} />
  }
  if (route.name === 'sets') {
    return <SetsPage client={client} onUnauthorized={onUnauthorized} activeSet={route.active} onSelectSet={selectSet} />
  }
  if (route.name === 'review') return <ReviewPage client={client} onUnauthorized={onUnauthorized} />
  if (route.name === 'admin') return <SetAdminPage client={client} onUnauthorized={onUnauthorized} />
  if (route.name === 'graph') {
    if (graphAnchorError !== null) {
      return (
        <p role="alert" className="mx-auto max-w-6xl px-6 py-10 text-sm text-red-600 dark:text-red-400">
          {graphAnchorError}
        </p>
      )
    }
    return (
      <GraphPage
        client={client}
        anchor={graphAnchor}
        onAnchorChange={openGraph}
        onOpenDocument={openDocument}
        onUnauthorized={onUnauthorized}
      />
    )
  }
  if (route.name === 'document') {
    return (
      <DocumentPage
        client={client}
        documentId={route.id}
        onOpenDocument={openDocument}
        onCenterGraph={openGraph}
        onBack={goBack}
        onUnauthorized={onUnauthorized}
      />
    )
  }
  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Page not found</h1>
      <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
        Nothing lives at{' '}
        <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs dark:bg-zinc-800">{route.path}</code>.
      </p>
      <Link
        href="/"
        onNavigate={() => {
          navigate('/')
        }}
        className="mt-4 inline-block text-sm text-emerald-700 hover:underline dark:text-emerald-400"
      >
        Back to search
      </Link>
    </div>
  )
}
