import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { GraphAnchor } from '../api/types'
import type { Route } from '../hooks/useRouter'
import { useRouter } from '../hooks/useRouter'
import { DocumentPage } from '../pages/DocumentPage'
import { DocumentsPage } from '../pages/DocumentsPage'
import { GraphPage } from '../pages/GraphPage'
import { PipelinePage } from '../pages/PipelinePage'
import { ReviewPage } from '../pages/ReviewPage'
import { SearchPage } from '../pages/SearchPage'
import { SetAdminPage } from '../pages/SetAdminPage'
import { SetsPage } from '../pages/SetsPage'
import { Link } from './Link'

interface NavItem {
  path: string
  label: string
  matches: Route['name']
}

// Two groups, because the six destinations do two different jobs: four are for reading the
// garden, two are for correcting it. A flat row of six gave every one of them the same weight.
const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: 'Explore',
    items: [
      { path: '/', label: 'Search', matches: 'search' },
      { path: '/documents', label: 'Documents', matches: 'documents' },
      { path: '/sets', label: 'Sets', matches: 'sets' },
      { path: '/graph', label: 'Graph', matches: 'graph' },
    ],
  },
  {
    label: 'Operate',
    items: [
      { path: '/pipeline', label: 'Pipeline', matches: 'pipeline' },
      { path: '/review', label: 'Review', matches: 'review' },
      { path: '/admin', label: 'Set admin', matches: 'admin' },
    ],
  },
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

  // On a phone the nav is one scrolling row, so the current destination can sit off-screen after
  // a route change. Pull it back into view instead of leaving the user to guess where they are.
  const navRef = useRef<HTMLElement | null>(null)
  useEffect(() => {
    navRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [route.name])

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[15rem_1fr]">
      <aside className="sticky top-0 z-20 border-b border-zinc-800 bg-zinc-950/85 backdrop-blur-md lg:flex lg:h-dvh lg:flex-col lg:border-r lg:border-b-0">
        <div className="flex h-14 items-center justify-between gap-3 px-5 lg:h-16">
          <Link
            href="/"
            onNavigate={() => {
              navigate('/')
            }}
            className="flex min-w-0 items-center gap-2 text-base font-semibold text-zinc-100"
          >
            <span aria-hidden="true" className="size-2 rounded-sm bg-emerald-500" />
            <span className="truncate">Links Garden</span>
          </Link>
          <SignOutButton onSignOut={onSignOut} className="lg:hidden" />
        </div>
        <nav
          ref={navRef}
          aria-label="Main"
          className="flex gap-1 overflow-x-auto px-3 pb-3 [scrollbar-width:none] lg:flex-1 lg:flex-col lg:gap-6 lg:overflow-y-auto lg:pb-6 [&::-webkit-scrollbar]:hidden"
        >
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="flex shrink-0 gap-1 lg:flex-col lg:gap-0.5">
              <p className="hud-label hidden px-3 pb-2 lg:block">{group.label}</p>
              {group.items.map((item) => (
                <NavLink
                  key={item.path}
                  item={item}
                  active={route.name === item.matches}
                  onNavigate={() => {
                    navigate(item.path)
                  }}
                />
              ))}
            </div>
          ))}
        </nav>
        <div className="hidden items-center justify-between gap-2 border-t border-zinc-800 px-4 py-4 lg:flex">
          <span className="hud-label flex items-center gap-2 whitespace-nowrap">
            <span aria-hidden="true" className="size-1.5 rounded-full bg-emerald-400" />
            session live
          </span>
          <SignOutButton onSignOut={onSignOut} />
        </div>
      </aside>
      <main className="min-w-0">
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

function NavLink({ item, active, onNavigate }: { item: NavItem; active: boolean; onNavigate: () => void }) {
  return (
    <Link
      href={item.path}
      onNavigate={onNavigate}
      aria-current={active ? 'page' : undefined}
      className={`rounded-md px-3 py-1.5 text-sm whitespace-nowrap transition-colors duration-150 ${
        active
          ? 'bg-zinc-800 font-medium text-zinc-50'
          : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-100'
      }`}
    >
      {item.label}
    </Link>
  )
}

function SignOutButton({ onSignOut, className = '' }: { onSignOut: () => void; className?: string }) {
  return (
    <button
      type="button"
      onClick={onSignOut}
      className={`cursor-pointer rounded-md border border-zinc-700 px-2.5 py-1 text-xs text-zinc-300 transition-colors duration-150 hover:bg-zinc-800 hover:text-zinc-100 whitespace-nowrap ${className}`}
    >
      Sign out
    </button>
  )
}

function GraphRoute({
  client,
  anchor,
  anchorError,
  onAnchorChange,
  onOpenDocument,
  onUnauthorized,
}: {
  client: ApiClient
  anchor: GraphAnchor | null
  anchorError: string | null
  onAnchorChange: (anchor: GraphAnchor) => void
  onOpenDocument: (id: number) => void
  onUnauthorized: () => void
}) {
  if (anchorError !== null) {
    return (
      <p role="alert" className="mx-auto max-w-6xl px-6 py-10 text-sm text-red-600 dark:text-red-400">
        {anchorError}
      </p>
    )
  }
  return (
    <GraphPage
      client={client}
      anchor={anchor}
      onAnchorChange={onAnchorChange}
      onOpenDocument={onOpenDocument}
      onUnauthorized={onUnauthorized}
    />
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
  if (route.name === 'pipeline') {
    return <PipelinePage client={client} onUnauthorized={onUnauthorized} onOpenDocument={openDocument} />
  }
  if (route.name === 'graph') {
    return (
      <GraphRoute
        client={client}
        anchor={graphAnchor}
        anchorError={graphAnchorError}
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
