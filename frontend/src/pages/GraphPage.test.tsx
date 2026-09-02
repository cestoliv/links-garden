import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../api/client'
import type { GraphAnchor, Hit } from '../api/types'
import { makeClient } from '../test/mockClient'
import { GraphPage } from './GraphPage'

function makeHit(overrides: Partial<Hit> = {}): Hit {
  return {
    document_id: 2,
    title: 'Doc B',
    url: 'https://example.test/b',
    source: 'obsidian',
    snippet: '',
    score: 0.5,
    fts_rank: null,
    vector_rank: 1,
    ...overrides,
  }
}

const anchorA: GraphAnchor = { id: 1, title: 'Doc A', url: 'https://example.test/a', embedded: true }

function noDocuments(): ApiClient['listDocuments'] {
  return vi.fn().mockResolvedValue({ items: [], next_cursor: null })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

describe('GraphPage', () => {
  it('shows a pick-an-anchor message when nothing is rooted yet', () => {
    render(
      <GraphPage
        client={makeClient({ listDocuments: noDocuments() })}
        anchor={null}
        onAnchorChange={vi.fn()}
        onOpenDocument={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    expect(screen.getByText(/pick a document/i)).toBeInTheDocument()
  })

  it('tells the user to run garden index rather than calling related for an unembedded anchor', async () => {
    const getRelated = vi.fn()
    render(
      <GraphPage
        client={makeClient({ listDocuments: noDocuments(), getRelated })}
        anchor={{ id: 1, title: 'Doc A', url: null, embedded: false }}
        onAnchorChange={vi.fn()}
        onOpenDocument={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    expect(await screen.findByText(/no embeddings yet/i)).toBeInTheDocument()
    expect(screen.getByText(/garden index/)).toBeInTheDocument()
    expect(getRelated).not.toHaveBeenCalled()
  })

  it('says so when the anchor is embedded but has no neighbours', async () => {
    const getRelated = vi.fn().mockResolvedValue([])
    render(
      <GraphPage
        client={makeClient({ listDocuments: noDocuments(), getRelated })}
        anchor={anchorA}
        onAnchorChange={vi.fn()}
        onOpenDocument={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    expect(await screen.findByText(/nothing else in the garden is near it/i)).toBeInTheDocument()
    // First hop only: an empty result short-circuits the second-hop fan-out.
    expect(getRelated).toHaveBeenCalledTimes(1)
  })

  it('de-duplicates nodes and edges shared across the two hops', async () => {
    const hitB = makeHit({ document_id: 2, title: 'Doc B' })
    const hitC = makeHit({ document_id: 3, title: 'Doc C' })
    const hitD = makeHit({ document_id: 4, title: 'Doc D' })
    const hitA = makeHit({ document_id: 1, title: 'Doc A' })

    // Anchor's first hop: B and C. B's second hop comes back with the anchor itself (A) and C
    // (another first-hop node) — both already-known ids. C's second hop introduces a genuinely
    // new node, D.
    const getRelated = vi.fn((id: number) => {
      if (id === 1) return Promise.resolve([hitB, hitC])
      if (id === 2) return Promise.resolve([hitA, hitC])
      if (id === 3) return Promise.resolve([hitB, hitD])
      return Promise.resolve([])
    })

    const { container } = render(
      <GraphPage
        client={makeClient({ listDocuments: noDocuments(), getRelated })}
        anchor={anchorA}
        onAnchorChange={vi.fn()}
        onOpenDocument={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    await screen.findByText('Doc D')
    // One node per document id: A, B, C, D — never a duplicate for the anchor or for C, even
    // though both reappear in a second-hop result.
    expect(container.querySelectorAll('svg g[transform]')).toHaveLength(4)
    // One edge per unordered pair: A-B, A-C, B-C, C-D. A-B/A-C from the first hop; B's "A" and
    // C's "B" edges collapse into those same two instead of doubling them.
    expect(container.querySelectorAll('svg line')).toHaveLength(4)
  })

  it('fans the second hop out concurrently instead of awaiting each call in turn', async () => {
    const hitB = makeHit({ document_id: 2, title: 'Doc B' })
    const hitC = makeHit({ document_id: 3, title: 'Doc C' })
    const pendingB = deferred<Hit[]>()
    const pendingC = deferred<Hit[]>()

    const getRelated = vi.fn((id: number) => {
      if (id === 1) return Promise.resolve([hitB, hitC])
      if (id === 2) return pendingB.promise
      if (id === 3) return pendingC.promise
      return Promise.resolve([])
    })

    render(
      <GraphPage
        client={makeClient({ listDocuments: noDocuments(), getRelated })}
        anchor={anchorA}
        onAnchorChange={vi.fn()}
        onOpenDocument={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    // Both second-hop calls start before either resolves: a serial loop would only have issued
    // the call for B at this point, still awaiting it before ever calling for C.
    await waitFor(() => {
      expect(getRelated).toHaveBeenCalledTimes(3)
    })
    expect(getRelated).toHaveBeenCalledWith(2, expect.any(Number))
    expect(getRelated).toHaveBeenCalledWith(3, expect.any(Number))

    pendingB.resolve([])
    pendingC.resolve([])
    await screen.findByText('Doc B')
  })

  function Harness({ client, initialAnchor }: { client: ApiClient; initialAnchor: GraphAnchor }) {
    const [anchor, setAnchor] = useState<GraphAnchor | null>(initialAnchor)
    return (
      <GraphPage client={client} anchor={anchor} onAnchorChange={setAnchor} onOpenDocument={vi.fn()} onUnauthorized={vi.fn()} />
    )
  }

  function withRecentDocuments(): ApiClient['listDocuments'] {
    return vi.fn().mockResolvedValue({
      items: [
        {
          id: 1,
          source: 'obsidian',
          source_ref: 'vault/a.md',
          url: 'https://example.test/a',
          title: 'Doc A',
          author: null,
          status: 'ok',
          error: null,
          fetched_at: '2026-01-01T00:00:00Z',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          embedded: true,
          enriched: false,
          set_names: [],
        },
        {
          id: 2,
          source: 'obsidian',
          source_ref: 'vault/b.md',
          url: 'https://example.test/b',
          title: 'Doc B',
          author: null,
          status: 'ok',
          error: null,
          fetched_at: '2026-01-01T00:00:00Z',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          embedded: true,
          enriched: false,
          set_names: [],
        },
      ],
      next_cursor: null,
    })
  }

  it('opens the document view on node click, without re-rooting the graph', async () => {
    const hitB = makeHit({ document_id: 2, title: 'Doc B' })
    const getRelated = vi.fn().mockResolvedValue([hitB])
    const onAnchorChange = vi.fn()
    const onOpenDocument = vi.fn()

    render(
      <GraphPage
        client={makeClient({ listDocuments: noDocuments(), getRelated })}
        anchor={anchorA}
        onAnchorChange={onAnchorChange}
        onOpenDocument={onOpenDocument}
        onUnauthorized={vi.fn()}
      />,
    )

    const nodeB = await screen.findByText('Doc B')
    await userEvent.click(nodeB)

    expect(onOpenDocument).toHaveBeenCalledWith(2)
    expect(onAnchorChange).not.toHaveBeenCalled()
  })

  it('re-roots via the recent-documents picker, and serves a previously-visited anchor from cache', async () => {
    const hitB = makeHit({ document_id: 2, title: 'Doc B' })
    const hitA = makeHit({ document_id: 1, title: 'Doc A' })
    // A's neighbours are just B, and B's are just A: a two-node graph either way it's rooted.
    const getRelated = vi.fn((id: number) => Promise.resolve(id === 1 ? [hitB] : id === 2 ? [hitA] : []))

    render(<Harness client={makeClient({ listDocuments: withRecentDocuments(), getRelated })} initialAnchor={anchorA} />)

    // "Doc A"/"Doc B" each show up twice once rooted (the picker entry and the graph's own SVG
    // node label), so the graph's own aria-label is the unambiguous way to assert which is rooted.
    await screen.findByRole('img', { name: 'Graph rooted on Doc A' })
    expect(getRelated).toHaveBeenCalledTimes(2) // A's first hop, then B's second hop

    await userEvent.click(screen.getByRole('link', { name: 'Doc B' }))

    // Rooted on B now, with A as its neighbour: two fresh calls for B's own two hops.
    await screen.findByRole('img', { name: 'Graph rooted on Doc B' })
    await waitFor(() => {
      expect(getRelated).toHaveBeenCalledTimes(4)
    })

    await userEvent.click(screen.getByRole('link', { name: 'Doc A' }))

    // Back on A, already computed once above — served from cache, no fifth call.
    await screen.findByRole('img', { name: 'Graph rooted on Doc A' })
    expect(getRelated).toHaveBeenCalledTimes(4)
  })
})
