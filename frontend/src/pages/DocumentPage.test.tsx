import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { Document, Hit } from '../api/types'
import { makeClient } from '../test/mockClient'
import { DocumentPage } from './DocumentPage'

function makeDocument(overrides: Partial<Document> = {}): Document {
  return {
    id: 1,
    source: 'obsidian',
    source_ref: 'vault/a.md',
    url: 'https://example.test/a',
    parent_document_id: null,
    title: 'Doc A',
    author: null,
    content: '# Heading\n\nBody text.',
    summary: null,
    keywords: null,
    message_text: null,
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

function makeHit(overrides: Partial<Hit> = {}): Hit {
  return {
    document_id: 2,
    title: 'Doc B',
    url: 'https://example.test/b',
    source: 'obsidian',
    snippet: '',
    score: 0.5,
    fts_rank: 1,
    vector_rank: null,
    ...overrides,
  }
}

describe('DocumentPage', () => {
  it('renders a markdown heading as a real heading element', async () => {
    const getDocument = vi.fn().mockResolvedValue(makeDocument({ content: '# Doc title\n\nSome text.' }))
    render(
      <DocumentPage
        client={makeClient({ getDocument, getRelated: vi.fn().mockResolvedValue([]) })}
        documentId={1}
        onOpenDocument={vi.fn()}
        onCenterGraph={vi.fn()}
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    expect(await screen.findByRole('heading', { level: 1, name: 'Doc title' })).toBeInTheDocument()
  })

  it('renders a GFM table as a real table, not literal markdown source', async () => {
    const content = ['| Name | Value |', '| --- | --- |', '| a | 1 |', '| b | 2 |'].join('\n')
    const getDocument = vi.fn().mockResolvedValue(makeDocument({ content }))
    render(
      <DocumentPage
        client={makeClient({ getDocument, getRelated: vi.fn().mockResolvedValue([]) })}
        documentId={1}
        onOpenDocument={vi.fn()}
        onCenterGraph={vi.fn()}
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Name' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'a' })).toBeInTheDocument()
    // The pipes and dashes of the source are gone, consumed as table syntax rather than shown as text.
    expect(screen.queryByText(/\|.*---/)).not.toBeInTheDocument()
  })

  it('never renders raw HTML in content as HTML, only as escaped text', async () => {
    const content = 'Before.\n\n<img src="x" onerror="alert(1)">\n\n<script>alert(1)</script>\n\nAfter.'
    const getDocument = vi.fn().mockResolvedValue(makeDocument({ content }))
    const { container } = render(
      <DocumentPage
        client={makeClient({ getDocument, getRelated: vi.fn().mockResolvedValue([]) })}
        documentId={1}
        onOpenDocument={vi.fn()}
        onCenterGraph={vi.fn()}
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    await screen.findByText('Before.')
    // No rehype-raw: react-markdown never parses embedded HTML into real elements, so neither
    // tag reaches the DOM as a live node — the core defense this view relies on.
    expect(container.querySelector('img')).not.toBeInTheDocument()
    expect(container.querySelector('script')).not.toBeInTheDocument()
  })

  it('shows the document error, not an empty pane, when content is null', async () => {
    const getDocument = vi.fn().mockResolvedValue(makeDocument({ content: null, error: 'Fetch failed: 404' }))
    render(
      <DocumentPage
        client={makeClient({ getDocument, getRelated: vi.fn().mockResolvedValue([]) })}
        documentId={1}
        onOpenDocument={vi.fn()}
        onCenterGraph={vi.fn()}
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('Fetch failed: 404')
  })

  it('falls back to a generic message when a null-content document has no error', async () => {
    const getDocument = vi.fn().mockResolvedValue(makeDocument({ content: null, error: null }))
    render(
      <DocumentPage
        client={makeClient({ getDocument, getRelated: vi.fn().mockResolvedValue([]) })}
        documentId={1}
        onOpenDocument={vi.fn()}
        onCenterGraph={vi.fn()}
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('This document has no content.')
  })

  it('shows an error state and calls onUnauthorized on a 401', async () => {
    const onUnauthorized = vi.fn()
    const getDocument = vi.fn().mockRejectedValue(new ApiError(401, 'unauthorized'))
    render(
      <DocumentPage
        client={makeClient({ getDocument, getRelated: vi.fn().mockResolvedValue([]) })}
        documentId={1}
        onOpenDocument={vi.fn()}
        onCenterGraph={vi.fn()}
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
      />,
    )

    await waitFor(() => {
      expect(onUnauthorized).toHaveBeenCalledTimes(1)
    })
  })

  it('centers the graph on this document, carrying its embedded status along', async () => {
    const onCenterGraph = vi.fn()
    const getDocument = vi
      .fn()
      .mockResolvedValue(makeDocument({ id: 7, title: 'Doc A', url: 'https://example.test/a', embedded: true }))
    render(
      <DocumentPage
        client={makeClient({ getDocument, getRelated: vi.fn().mockResolvedValue([]) })}
        documentId={7}
        onOpenDocument={vi.fn()}
        onCenterGraph={onCenterGraph}
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )
    await screen.findByRole('heading', { name: 'Doc A' })

    await userEvent.click(screen.getByRole('button', { name: 'Center the graph on this document' }))

    expect(onCenterGraph).toHaveBeenCalledWith({ id: 7, title: 'Doc A', url: 'https://example.test/a', embedded: true })
  })

  it('opens a neighbour document from the related list', async () => {
    const onOpenDocument = vi.fn()
    const getRelated = vi.fn().mockResolvedValue([makeHit({ document_id: 9, title: 'Doc C' })])
    render(
      <DocumentPage
        client={makeClient({ getDocument: vi.fn().mockResolvedValue(makeDocument()), getRelated })}
        documentId={1}
        onOpenDocument={onOpenDocument}
        onCenterGraph={vi.fn()}
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: 'Doc C' }))

    expect(onOpenDocument).toHaveBeenCalledWith(9)
  })

  it('calls onBack when the back control is clicked', async () => {
    const onBack = vi.fn()
    render(
      <DocumentPage
        client={makeClient({ getDocument: vi.fn().mockResolvedValue(makeDocument()), getRelated: vi.fn().mockResolvedValue([]) })}
        documentId={1}
        onOpenDocument={vi.fn()}
        onCenterGraph={vi.fn()}
        onBack={onBack}
        onUnauthorized={vi.fn()}
      />,
    )
    await screen.findByRole('heading', { name: 'Doc A' })

    await userEvent.click(screen.getByRole('button', { name: 'Back' }))

    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('shows the generated summary and keywords in a distinct panel', async () => {
    const getDocument = vi.fn().mockResolvedValue(makeDocument({ summary: 'A short summary.', keywords: 'a, b, c' }))
    render(
      <DocumentPage
        client={makeClient({ getDocument, getRelated: vi.fn().mockResolvedValue([]) })}
        documentId={1}
        onOpenDocument={vi.fn()}
        onCenterGraph={vi.fn()}
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
      />,
    )

    expect(await screen.findByText('A short summary.')).toBeInTheDocument()
    expect(screen.getByText(/Keywords: a, b, c/)).toBeInTheDocument()
  })
})
