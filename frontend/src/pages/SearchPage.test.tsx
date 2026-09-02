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
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} onOpenDocument={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'tiktok slideshow')

    await waitFor(() => expect(screen.getByText('Tiktok slideshow ideas')).toBeInTheDocument(), {
      timeout: 2000,
    })
    expect(search).toHaveBeenCalledWith('tiktok slideshow', 20)
  })

  it('shows the empty state for no results', async () => {
    const search = vi.fn().mockResolvedValue([])
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} onOpenDocument={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'nothing here')

    await waitFor(() => expect(screen.getByText(/No results for/)).toBeInTheDocument(), {
      timeout: 2000,
    })
  })

  it('wraps unbreakable snippet text instead of overflowing the card', async () => {
    const search = vi.fn().mockResolvedValue([hit])
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} onOpenDocument={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'tiktok slideshow')

    const snippet = await screen.findByText(hit.snippet)
    expect(snippet.className).toContain('break-words')
  })

  it('warns when the top result is no better than a single-sided rank-1 match', async () => {
    const weakHit: Hit = { ...hit, score: 1 / 61, vector_rank: null }
    const search = vi.fn().mockResolvedValue([weakHit])
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} onOpenDocument={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'zzzqqq')

    await waitFor(() => expect(screen.getByText(/No strong match for/)).toBeInTheDocument(), {
      timeout: 2000,
    })
    // Still shown, just flagged as weak — the fix must not hide or filter results.
    expect(screen.getByText('Tiktok slideshow ideas')).toBeInTheDocument()
  })

  it('does not warn when the top result matched both search sides at rank 1', async () => {
    const search = vi.fn().mockResolvedValue([hit])
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} onOpenDocument={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search'), 'tiktok slideshow')

    await waitFor(() => expect(screen.getByText('Tiktok slideshow ideas')).toBeInTheDocument(), {
      timeout: 2000,
    })
    expect(screen.queryByText(/No strong match for/)).not.toBeInTheDocument()
  })

  it('opens the document view on the result title, url or not', async () => {
    const noUrlHit: Hit = { ...hit, document_id: 2, url: null }
    const search = vi.fn().mockResolvedValue([hit, noUrlHit])
    const onOpenDocument = vi.fn()
    render(<SearchPage client={makeClient({ search })} onUnauthorized={vi.fn()} onOpenDocument={onOpenDocument} />)

    await userEvent.type(screen.getByLabelText('Search'), 'tiktok slideshow')
    const titles = await screen.findAllByText('Tiktok slideshow ideas')
    expect(titles).toHaveLength(2)
    // Neither entry links out directly, url or not: both are buttons into the document view.
    for (const title of titles) expect(title.tagName).toBe('BUTTON')

    await userEvent.click(titles[0])
    expect(onOpenDocument).toHaveBeenCalledWith(1)

    await userEvent.click(titles[1])
    expect(onOpenDocument).toHaveBeenCalledWith(2)
  })
})
