import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { DocumentListItem, GraphAnchor, Hit } from '../api/types'

interface GraphPageProps {
  client: ApiClient
  anchor: GraphAnchor | null
  onAnchorChange: (anchor: GraphAnchor) => void
  onOpenDocument: (id: number) => void
  onUnauthorized: () => void
}

// See DESIGN.md: "a graph showing one item's neighbors two hops out". Ten first-hop calls times
// a smaller second-hop limit keeps a click's total fan-out at eleven requests — one first hop
// plus one second-hop call per first-hop neighbour, run concurrently.
const FIRST_HOP_LIMIT = 10
const SECOND_HOP_LIMIT = 5
const RECENT_DOCUMENTS_LIMIT = 20

interface GraphNode {
  id: number
  title: string | null
  url: string | null
  hop: 0 | 1 | 2
}

interface GraphEdge {
  a: number
  b: number
}

interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

type GraphState =
  | { status: 'idle' }
  | { status: 'not-embedded' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: GraphData }

/** One first-hop call, then a concurrent `Promise.all` for the second hop — never a serial loop.
 * De-dupes nodes by document id (keeping the shortest hop distance already found) and edges by
 * unordered pair, since the anchor reappears as a neighbour of its own neighbours and first-hop
 * nodes routinely share a second-hop node. */
async function fetchGraph(client: ApiClient, anchor: GraphAnchor): Promise<GraphData> {
  const nodes = new Map<number, GraphNode>()
  nodes.set(anchor.id, { id: anchor.id, title: anchor.title, url: anchor.url, hop: 0 })
  const edges = new Map<string, GraphEdge>()

  const addEdge = (a: number, b: number) => {
    if (a === b) return
    const key = a < b ? `${String(a)}:${String(b)}` : `${String(b)}:${String(a)}`
    if (!edges.has(key)) edges.set(key, { a, b })
  }
  const addNode = (hit: Hit, hop: 1 | 2) => {
    if (!nodes.has(hit.document_id)) {
      nodes.set(hit.document_id, { id: hit.document_id, title: hit.title, url: hit.url, hop })
    }
  }

  const firstHop = await client.getRelated(anchor.id, FIRST_HOP_LIMIT)
  for (const hit of firstHop) {
    addNode(hit, 1)
    addEdge(anchor.id, hit.document_id)
  }

  const firstHopIds = firstHop.map((hit) => hit.document_id)
  const secondHop = await Promise.all(firstHopIds.map((id) => client.getRelated(id, SECOND_HOP_LIMIT)))
  secondHop.forEach((hits, index) => {
    const sourceId = firstHopIds[index]
    for (const hit of hits) {
      addNode(hit, 2)
      addEdge(sourceId, hit.document_id)
    }
  })

  return { nodes: [...nodes.values()], edges: [...edges.values()] }
}

export function GraphPage({ client, anchor, onAnchorChange, onOpenDocument, onUnauthorized }: GraphPageProps) {
  const [state, setState] = useState<GraphState>({ status: 'idle' })
  // Per-anchor within the session, not a library: re-clicking a node already visited this run
  // reuses the computed graph instead of re-fetching it.
  const cacheRef = useRef(new Map<number, GraphData>())
  const reduceMotion = useReducedMotion()

  useEffect(() => {
    if (anchor === null) {
      setState({ status: 'idle' })
      return
    }
    if (!anchor.embedded) {
      setState({ status: 'not-embedded' })
      return
    }
    const cached = cacheRef.current.get(anchor.id)
    if (cached !== undefined) {
      setState({ status: 'ready', data: cached })
      return
    }
    let cancelled = false
    setState({ status: 'loading' })
    fetchGraph(client, anchor)
      .then((data) => {
        if (cancelled) return
        cacheRef.current.set(anchor.id, data)
        setState({ status: 'ready', data })
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
  }, [anchor, client, onUnauthorized])

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Graph</h1>
      <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
        One document’s neighbours, two hops out.
      </p>
      <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[260px_1fr]">
        <RecentDocuments client={client} onUnauthorized={onUnauthorized} onPick={onAnchorChange} activeId={anchor?.id ?? null} />
        <GraphCanvas state={state} anchor={anchor} reduceMotion={reduceMotion} onNodeClick={onOpenDocument} />
      </div>
    </div>
  )
}

type RecentState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; items: DocumentListItem[] }

/** The graph page's own anchor picker: a short, one-shot list rather than the search box, so
 * every entry already carries `embedded` — the flag the empty states below need — with no extra
 * round trip. The Documents page's own "Graph" control is the other entry point. */
function RecentDocuments({
  client,
  onUnauthorized,
  onPick,
  activeId,
}: {
  client: ApiClient
  onUnauthorized: () => void
  onPick: (anchor: GraphAnchor) => void
  activeId: number | null
}) {
  const [state, setState] = useState<RecentState>({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    client
      .listDocuments({ limit: RECENT_DOCUMENTS_LIMIT })
      .then((page) => {
        if (!cancelled) setState({ status: 'ready', items: page.items })
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
  }, [client, onUnauthorized])

  return (
    <div>
      <h2 className="text-xs font-medium tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
        Recent documents
      </h2>
      {state.status === 'loading' && <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">Loading…</p>}
      {state.status === 'error' && (
        <p role="alert" className="mt-2 text-sm text-red-600 dark:text-red-400">
          {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <ul className="mt-2 flex flex-col gap-1">
          {state.items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => {
                  onPick({ id: item.id, title: item.title, url: item.url, embedded: item.embedded })
                }}
                aria-current={activeId === item.id ? 'true' : undefined}
                className={`w-full truncate rounded-md px-2 py-1.5 text-left text-sm transition-colors duration-150 ${
                  activeId === item.id
                    ? 'bg-emerald-700 text-white dark:bg-emerald-600'
                    : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800'
                }`}
              >
                {item.title ?? item.url ?? 'Untitled'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

const VIEW_SIZE = 600
const CENTER = VIEW_SIZE / 2
const RING_RADIUS: Record<1 | 2, number> = { 1: 140, 2: 260 }
// A label is centred under its node, so the outer ring's leftmost and rightmost labels hang past
// the layout's own bounds: a node at `CENTER - 260` sits at x = 40 and its label reaches x = -15,
// which the SVG edge then clips mid-word. The viewBox is widened by this much on each side so
// those labels stay readable. It pads the view, not the layout, so no node position changes.
const LABEL_MARGIN = 70
const NODE_RADIUS: Record<0 | 1 | 2, number> = { 0: 14, 1: 10, 2: 7 }
const NODE_FILL: Record<0 | 1 | 2, string> = {
  0: 'fill-emerald-700 dark:fill-emerald-500',
  1: 'fill-emerald-400 dark:fill-emerald-600',
  2: 'fill-zinc-400 dark:fill-zinc-600',
}

/** Radial layout, no simulation: the anchor at the centre, then each ring's nodes spaced evenly
 * by angle. Cheap trigonometry is enough for the handful of nodes a two-hop fan-out produces. */
function layoutNodes(anchorId: number, nodes: GraphNode[]): Map<number, { x: number; y: number }> {
  const positions = new Map<number, { x: number; y: number }>()
  positions.set(anchorId, { x: CENTER, y: CENTER })
  for (const hop of [1, 2] as const) {
    const ring = nodes.filter((node) => node.hop === hop)
    ring.forEach((node, index) => {
      const angle = (2 * Math.PI * index) / ring.length - Math.PI / 2
      positions.set(node.id, {
        x: CENTER + RING_RADIUS[hop] * Math.cos(angle),
        y: CENTER + RING_RADIUS[hop] * Math.sin(angle),
      })
    })
  }
  return positions
}

function truncateTitle(text: string, max = 22): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

function anchorLabel(anchor: GraphAnchor | null): string {
  return anchor?.title ?? anchor?.url ?? 'This document'
}

function GraphCanvas({
  state,
  anchor,
  reduceMotion,
  onNodeClick,
}: {
  state: GraphState
  anchor: GraphAnchor | null
  reduceMotion: boolean | null
  onNodeClick: (id: number) => void
}) {
  if (state.status === 'idle') {
    return <EmptyPanel>Pick a document on the left to see its neighbours.</EmptyPanel>
  }
  if (state.status === 'not-embedded') {
    return (
      <EmptyPanel>
        {anchorLabel(anchor)} has no embeddings yet. Run{' '}
        <code className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs dark:bg-zinc-800">garden index</code> to
        include it in the graph.
      </EmptyPanel>
    )
  }
  if (state.status === 'loading') {
    return <EmptyPanel>Loading graph…</EmptyPanel>
  }
  if (state.status === 'error') {
    return (
      <EmptyPanel role="alert" tone="error">
        {state.message}
      </EmptyPanel>
    )
  }
  if (anchor === null) return null // unreachable: 'ready' only follows a non-null anchor above

  const { nodes, edges } = state.data
  if (nodes.length === 1) {
    return <EmptyPanel>{anchorLabel(anchor)} is embedded, but nothing else in the garden is near it yet.</EmptyPanel>
  }

  const positions = layoutNodes(anchor.id, nodes)

  return (
    <AnimatePresence mode="wait">
      <motion.svg
        key={anchor.id}
        viewBox={`${String(-LABEL_MARGIN)} 0 ${String(VIEW_SIZE + LABEL_MARGIN * 2)} ${String(VIEW_SIZE)}`}
        initial={reduceMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.2 }}
        className="mx-auto w-full max-w-2xl"
        role="img"
        aria-label={`Graph rooted on ${anchorLabel(anchor)}`}
      >
        <g className="stroke-zinc-200 dark:stroke-zinc-800">
          {edges.map((edge) => {
            const from = positions.get(edge.a)
            const to = positions.get(edge.b)
            if (from === undefined || to === undefined) return null
            return (
              <line key={`${String(edge.a)}-${String(edge.b)}`} x1={from.x} y1={from.y} x2={to.x} y2={to.y} strokeWidth={1} />
            )
          })}
        </g>
        {nodes.map((node) => {
          const pos = positions.get(node.id)
          if (pos === undefined) return null
          const isAnchor = node.hop === 0
          return (
            <g
              key={node.id}
              transform={`translate(${String(pos.x)}, ${String(pos.y)})`}
              className={isAnchor ? undefined : 'cursor-pointer'}
              onClick={
                isAnchor
                  ? undefined
                  : () => {
                      onNodeClick(node.id)
                    }
              }
            >
              <circle r={NODE_RADIUS[node.hop]} className={NODE_FILL[node.hop]} />
              <text y={NODE_RADIUS[node.hop] + 14} textAnchor="middle" className="fill-zinc-700 text-[10px] dark:fill-zinc-300">
                {truncateTitle(node.title ?? node.url ?? 'Untitled')}
              </text>
            </g>
          )
        })}
      </motion.svg>
    </AnimatePresence>
  )
}

function EmptyPanel({ children, role, tone }: { children: ReactNode; role?: 'alert'; tone?: 'error' }) {
  return (
    <div
      role={role}
      className={`flex min-h-80 items-center justify-center rounded-lg border border-dashed border-zinc-200 px-6 text-center text-sm dark:border-zinc-800 ${
        tone === 'error' ? 'text-red-600 dark:text-red-400' : 'text-zinc-500 dark:text-zinc-400'
      }`}
    >
      <p>{children}</p>
    </div>
  )
}
