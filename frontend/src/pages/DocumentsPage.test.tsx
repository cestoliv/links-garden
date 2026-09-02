import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { DocumentListItem, DocumentListPage } from '../api/types'
import { makeClient } from '../test/mockClient'
import { DocumentsPage } from './DocumentsPage'

function makeItem(overrides: Partial<DocumentListItem> = {}): DocumentListItem {
  return {
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
    ...overrides,
  }
}

/** jsdom has no real IntersectionObserver; this stands in for it so a test can drive the
 * sentinel's intersection state directly instead of relying on real layout/scrolling. */
class MockIntersectionObserver {
  static instances: MockIntersectionObserver[] = []
  callback: IntersectionObserverCallback
  observe = vi.fn()
  disconnect = vi.fn()
  unobserve = vi.fn()

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    MockIntersectionObserver.instances.push(this)
  }

  trigger(isIntersecting: boolean) {
    this.callback([{ isIntersecting } as IntersectionObserverEntry], this as unknown as IntersectionObserver)
  }
}

function latestObserver(): MockIntersectionObserver {
  const instance = MockIntersectionObserver.instances.at(-1)
  if (instance === undefined) throw new Error('no IntersectionObserver was created')
  return instance
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

afterEach(() => {
  MockIntersectionObserver.instances = []
  vi.unstubAllGlobals()
})

describe('DocumentsPage', () => {
  it('renders the first page, requesting the default limit and no cursor', async () => {
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const listDocuments = vi.fn().mockResolvedValue({
      items: [makeItem({ id: 1, title: 'Doc A' })],
      next_cursor: null,
    } satisfies DocumentListPage)

    render(<DocumentsPage client={makeClient({ listDocuments })} onUnauthorized={vi.fn()} onOpenGraph={vi.fn()} onOpenDocument={vi.fn()} />)

    expect(await screen.findByText('Doc A')).toBeInTheDocument()
    expect(listDocuments).toHaveBeenCalledWith({ limit: 50, cursor: undefined })
  })

  it('shows the empty state when there are no documents', async () => {
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const listDocuments = vi.fn().mockResolvedValue({ items: [], next_cursor: null })

    render(<DocumentsPage client={makeClient({ listDocuments })} onUnauthorized={vi.fn()} onOpenGraph={vi.fn()} onOpenDocument={vi.fn()} />)

    expect(await screen.findByText('No documents yet.')).toBeInTheDocument()
  })

  it('shows an error state and calls onUnauthorized on a 401', async () => {
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const onUnauthorized = vi.fn()
    const listDocuments = vi.fn().mockRejectedValue(new ApiError(401, 'unauthorized'))

    render(<DocumentsPage client={makeClient({ listDocuments })} onUnauthorized={onUnauthorized} onOpenGraph={vi.fn()} onOpenDocument={vi.fn()} />)

    await waitFor(() => { expect(onUnauthorized).toHaveBeenCalledTimes(1); })
  })

  it('loads the next page once the sentinel intersects, and ignores a repeat trigger mid-flight', async () => {
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const firstPage: DocumentListPage = { items: [makeItem({ id: 1 })], next_cursor: 'cursor-1' }
    const second = deferred<DocumentListPage>()
    const listDocuments = vi.fn().mockResolvedValueOnce(firstPage).mockReturnValueOnce(second.promise)

    render(<DocumentsPage client={makeClient({ listDocuments })} onUnauthorized={vi.fn()} onOpenGraph={vi.fn()} onOpenDocument={vi.fn()} />)
    // Wait for the rendered page, not just the call count: the mock call fires synchronously
    // on mount, well before the promise resolves and the 'ready' state (and its observer) exist.
    await screen.findByText('Doc A')

    const observer = latestObserver()
    observer.trigger(true)
    observer.trigger(true) // fires again while the request above is still in flight

    await waitFor(() => { expect(listDocuments).toHaveBeenCalledTimes(2); })
    expect(listDocuments).toHaveBeenNthCalledWith(2, { limit: 50, cursor: 'cursor-1' })

    second.resolve({ items: [makeItem({ id: 2, title: 'Doc B' })], next_cursor: null })
    expect(await screen.findByText('Doc B')).toBeInTheDocument()
    // Still just the two calls above: the mid-flight repeat trigger never started a third.
    expect(listDocuments).toHaveBeenCalledTimes(2)
  })

  it('disconnects the observer once the last page has loaded and does not fetch again', async () => {
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const listDocuments = vi
      .fn()
      .mockResolvedValueOnce({ items: [makeItem({ id: 1 })], next_cursor: 'cursor-1' })
      .mockResolvedValueOnce({ items: [makeItem({ id: 2 })], next_cursor: null })

    render(<DocumentsPage client={makeClient({ listDocuments })} onUnauthorized={vi.fn()} onOpenGraph={vi.fn()} onOpenDocument={vi.fn()} />)
    await screen.findByText('Doc A')
    expect(MockIntersectionObserver.instances).toHaveLength(1)
    const observer = latestObserver()

    observer.trigger(true)
    await waitFor(() => { expect(listDocuments).toHaveBeenCalledTimes(2); })
    await waitFor(() => { expect(observer.disconnect).toHaveBeenCalledTimes(1); })

    // No replacement observer was created for the now-exhausted list.
    expect(MockIntersectionObserver.instances).toHaveLength(1)

    // Even a stray trigger on the disconnected observer must not start another request.
    observer.trigger(true)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(listDocuments).toHaveBeenCalledTimes(2)
  })

  it('opens the graph rooted on a row, carrying its embedded status along', async () => {
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const listDocuments = vi.fn().mockResolvedValue({
      items: [makeItem({ id: 7, title: 'Doc A', url: 'https://example.test/a', embedded: true })],
      next_cursor: null,
    })
    const onOpenGraph = vi.fn()

    render(<DocumentsPage client={makeClient({ listDocuments })} onUnauthorized={vi.fn()} onOpenGraph={onOpenGraph} onOpenDocument={vi.fn()} />)
    await screen.findByText('Doc A')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Graph' }))

    expect(onOpenGraph).toHaveBeenCalledWith({ id: 7, title: 'Doc A', url: 'https://example.test/a', embedded: true })
  })

  it('opens the document view when a row title is clicked', async () => {
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver)
    const listDocuments = vi.fn().mockResolvedValue({
      items: [makeItem({ id: 7, title: 'Doc A' })],
      next_cursor: null,
    })
    const onOpenDocument = vi.fn()

    render(<DocumentsPage client={makeClient({ listDocuments })} onUnauthorized={vi.fn()} onOpenGraph={vi.fn()} onOpenDocument={onOpenDocument} />)
    await screen.findByText('Doc A')

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Doc A' }))

    expect(onOpenDocument).toHaveBeenCalledWith(7)
  })
})
