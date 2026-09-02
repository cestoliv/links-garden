import { useCallback, useEffect, useState } from 'react'

// Every path the dashboard understands. `document`/`graph` carry a numeric id parsed from the
// URL; `sets` carries the tab name (or null for the bare /sets default); `search` carries its
// query so the search box itself is shareable. Anything else is `not-found`, rendered inside the
// shell rather than left blank.
export type Route =
  | { name: 'search'; query: string }
  | { name: 'documents' }
  | { name: 'document'; id: number }
  | { name: 'sets'; active: string | null }
  | { name: 'review' }
  | { name: 'admin' }
  | { name: 'graph'; anchorId: number | null }
  | { name: 'not-found'; path: string }

// The routes that take no parameter. Typed with `| undefined` on purpose: without
// `noUncheckedIndexedAccess`, indexing a plain Record hands back a non-optional `Route` and every
// guard against a missing key then reads to the type checker as dead code.
const STATIC_ROUTES: Record<string, Route | undefined> = {
  documents: { name: 'documents' },
  review: { name: 'review' },
  admin: { name: 'admin' },
  graph: { name: 'graph', anchorId: null },
}

/** A positive integer id, or null when the segment is not one.
 *
 * `Number('')` is 0 and `Number('12abc')` is NaN, so an empty or malformed segment resolves to
 * not-found here rather than travelling on to the API as a nonsense id. */
function parseId(segment: string): number | null {
  const id = Number(segment)
  return Number.isInteger(id) && id > 0 ? id : null
}

/** Parses a location into a `Route`. Each path segment is percent-decoded on its own (not the
 * whole pathname at once), matching how `client.ts` already encodes a set name for its own URL.
 *
 * `.at()` rather than destructuring: it returns `string | undefined`, which is the truth about
 * indexing an array of unknown length and is what lets the checks below type-check. */
export function parseRoute(location: { pathname: string; search: string }): Route {
  const segments = location.pathname.split('/').filter(Boolean).map(decodeURIComponent)
  const head = segments.at(0)
  const sub = segments.at(1)
  const notFound: Route = { name: 'not-found', path: location.pathname }

  if (head === undefined) {
    return { name: 'search', query: new URLSearchParams(location.search).get('q') ?? '' }
  }
  // /sets/:name takes an arbitrary set name, so it cannot go through the numeric id path below.
  if (head === 'sets') return { name: 'sets', active: sub ?? null }
  if (sub === undefined) return STATIC_ROUTES[head] ?? notFound

  const id = parseId(sub)
  if (id === null) return notFound
  if (head === 'documents') return { name: 'document', id }
  if (head === 'graph') return { name: 'graph', anchorId: id }
  return notFound
}

function readRoute(): Route {
  return parseRoute(window.location)
}

export interface NavigateOptions {
  /** Updates the current history entry instead of pushing a new one — for a live-typed query,
   * where every keystroke pushing its own entry would make Back step through characters. */
  replace?: boolean
}

/** History-API routing: no react-router, no Context. One call at the shell's root, threaded down
 * as props exactly like `client` — the same amount of cross-page wiring the dashboard used before,
 * just driven by the URL instead of `useState`. */
export function useRouter(): { route: Route; navigate: (path: string, options?: NavigateOptions) => void } {
  const [route, setRoute] = useState<Route>(readRoute)

  useEffect(() => {
    const onPopState = () => {
      setRoute(readRoute())
    }
    window.addEventListener('popstate', onPopState)
    return () => {
      window.removeEventListener('popstate', onPopState)
    }
  }, [])

  const navigate = useCallback((path: string, options: NavigateOptions = {}) => {
    if (path === window.location.pathname + window.location.search) return
    if (options.replace) {
      window.history.replaceState(null, '', path)
    } else {
      window.history.pushState(null, '', path)
    }
    // pushState/replaceState never fire popstate on their own, so the new route has to be read
    // back explicitly — only Back/Forward reach the listener above.
    setRoute(readRoute())
  }, [])

  return { route, navigate }
}
