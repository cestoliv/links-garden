import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Hit } from '../api/types'
import { makeClient } from '../test/mockClient'
import { SearchPage } from './SearchPage'

const hit: Hit = {
  document_id: 1,
  title: 'Tiktok slideshow ideas',
  url: 'https://example.com/a',
  source: 'obsidian',
  snippet: 'Une liste d’idées de diaporamas…',
  score: 0.0317,
  fts_rank: 1,
  vector_rank: 1,
}

describe('SearchPage', () => {
  it('renders results for a query', async () => {
    const search = vi.fn().mockResolvedValue([hit])
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'tiktok slideshow')

    await waitFor(() => expect(screen.getByText('Tiktok slideshow ideas')).toBeInTheDocument(), {
      timeout: 2000,
    })
    expect(search).toHaveBeenCalledWith('tiktok slideshow', 20)
  })

  it('shows the empty state for no results', async () => {
    const search = vi.fn().mockResolvedValue([])
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'nothing here')

    await waitFor(() => expect(screen.getByText(/No results for/)).toBeInTheDocument(), {
      timeout: 2000,
    })
  })

  it('wraps unbreakable snippet text instead of overflowing the card', async () => {
    const search = vi.fn().mockResolvedValue([hit])
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'tiktok slideshow')

    const snippet = await screen.findByText(hit.snippet)
    expect(snippet.className).toContain('break-words')
  })

  it('warns when the top result is no better than a single-sided rank-1 match', async () => {
    const weakHit: Hit = { ...hit, score: 1 / 61, vector_rank: null }
    const search = vi.fn().mockResolvedValue([weakHit])
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'zzzqqq')

    await waitFor(() => expect(screen.getByText(/No strong match for/)).toBeInTheDocument(), {
      timeout: 2000,
    })
    // Still shown, just flagged as weak — the fix must not hide or filter results.
    expect(screen.getByText('Tiktok slideshow ideas')).toBeInTheDocument()
  })

  it('does not warn when the top result matched both search sides at rank 1', async () => {
    const search = vi.fn().mockResolvedValue([hit])
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'tiktok slideshow')

    await waitFor(() => expect(screen.getByText('Tiktok slideshow ideas')).toBeInTheDocument(), {
      timeout: 2000,
    })
    expect(screen.queryByText(/No strong match for/)).not.toBeInTheDocument()
  })

  it('expands a url-less result in place to show the document', async () => {
    const noUrlHit: Hit = { ...hit, url: null }
    const search = vi.fn().mockResolvedValue([noUrlHit])
    const getDocument = vi.fn().mockResolvedValue({
      id: 1,
      source: 'obsidian',
      source_ref: 'vault/tiktok.md',
      url: null,
      parent_document_id: null,
      title: 'Tiktok slideshow ideas',
      author: null,
      content: 'Full note content goes here.',
      summary: null,
      keywords: null,
      message_text: null,
      status: 'ready',
      error: null,
      fetched_at: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    })
    render(
      <SearchPage client={makeClient({ search, getDocument })} onUnauthorized={vi.fn()} />,
    )

    await userEvent.type(screen.getByLabelText('Search'), 'tiktok slideshow')
    const title = await screen.findByText('Tiktok slideshow ideas')

    // No URL, so the title is a plain button, not a link.
    expect(title.tagName).toBe('BUTTON')

    await userEvent.click(title)

    expect(getDocument).toHaveBeenCalledWith(1)
    expect(await screen.findByText('Full note content goes here.')).toBeInTheDocument()
    expect(screen.getByText('vault/tiktok.md')).toBeInTheDocument()
  })
})
