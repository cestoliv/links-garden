import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Pipeline } from '../api/types'
import { makeClient } from '../test/mockClient'
import { PipelinePage } from './PipelinePage'

const pipeline: Pipeline = {
  run: null,
  stages: [
    {
      stage: 'fetch',
      done: 8,
      waiting: 1,
      failed: 1,
      items: [
        {
          document_id: 3,
          title: 'Broken page',
          url: 'https://example.test/broken',
          source: 'obsidian',
          state: 'failed',
          detail: 'Fetch failed: 404',
        },
        { document_id: 4, title: 'Queued page', url: null, source: 'manual', state: 'waiting', detail: null },
      ],
    },
    { stage: 'embed', done: 8, waiting: 0, failed: 0, items: [] },
    { stage: 'enrich', done: 0, waiting: 0, failed: 0, items: [] },
    {
      stage: 'extract',
      done: 1,
      waiting: 12,
      failed: 0,
      items: [
        { document_id: 9, title: 'Pasta', url: null, source: 'obsidian', state: 'waiting', detail: 'recipe' },
      ],
    },
  ],
}

function renderPage(overrides: Partial<Pipeline> = {}, onOpenDocument = vi.fn()) {
  const getPipeline = vi.fn().mockResolvedValue({ ...pipeline, ...overrides })
  const retryStage = vi.fn().mockResolvedValue({
    stage: 'embed',
    state: 'running',
    started_at: '2026-09-11T10:00:00Z',
    finished_at: null,
    detail: null,
  })
  render(
    <PipelinePage
      client={makeClient({ getPipeline, retryStage })}
      onUnauthorized={vi.fn()}
      onOpenDocument={onOpenDocument}
    />,
  )
  return { getPipeline, retryStage, onOpenDocument }
}

function stageSection(label: string): HTMLElement {
  const heading = screen.getByRole('heading', { name: label })
  const section = heading.closest('section')
  if (section === null) throw new Error(`no section around the ${label} heading`)
  return section
}

describe('PipelinePage', () => {
  it('shows each stage with its counts and its queued documents', async () => {
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Fetch' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Broken page' })).toHaveAttribute('href', '/documents/3')
    expect(screen.getByText('Fetch failed: 404')).toBeInTheDocument()
    // The set name is what says which extraction a document is waiting on.
    expect(screen.getByText('recipe')).toBeInTheDocument()
  })

  it('reports progress as a share of the documents eligible for that stage', async () => {
    renderPage()

    const fetchBar = await screen.findByRole('progressbar', { name: 'Fetch progress' })
    expect(fetchBar).toHaveAttribute('aria-valuenow', '80')
    // Nothing eligible yet is 0%, not a division by zero.
    expect(screen.getByRole('progressbar', { name: 'Enrich progress' })).toHaveAttribute('aria-valuenow', '0')
  })

  it('says how many queued documents the sample leaves out', async () => {
    renderPage()

    expect(await screen.findByText('+11 more queued')).toBeInTheDocument()
  })

  it('distinguishes an empty queue from a stage nothing is eligible for', async () => {
    renderPage()

    expect(await screen.findByText(/Queue empty/)).toBeInTheDocument()
    expect(screen.getByText('Nothing eligible for this stage yet.')).toBeInTheDocument()
  })

  it('opens a queued document', async () => {
    const { onOpenDocument } = renderPage()

    await userEvent.click(await screen.findByRole('link', { name: 'Broken page' }))

    expect(onOpenDocument).toHaveBeenCalledWith(3)
  })

  it('runs the stage the button belongs to', async () => {
    const { retryStage } = renderPage()
    await screen.findByRole('heading', { name: 'Enrich' })

    await userEvent.click(
      within(stageSection('Enrich')).getByRole('button', { name: 'Run stage' }),
    )

    expect(retryStage).toHaveBeenCalledWith('enrich')
  })

  // The API runs one stage at a time, so a second button would only earn a 409.
  it('locks every stage button while a run is in flight', async () => {
    renderPage({
      run: {
        stage: 'embed',
        state: 'running',
        started_at: '2026-09-11T10:00:00Z',
        finished_at: null,
        detail: null,
      },
    })
    await screen.findByRole('heading', { name: 'Embed' })

    expect(within(stageSection('Embed')).getByRole('button', { name: 'Running…' })).toBeDisabled()
    expect(within(stageSection('Fetch')).getByRole('button', { name: 'Run stage' })).toBeDisabled()
  })

  // The run finishes long after its own request, so its outcome can only arrive with a poll.
  it('reports the last run on the stage it ran', async () => {
    renderPage({
      run: {
        stage: 'embed',
        state: 'error',
        started_at: '2026-09-11T10:00:00Z',
        finished_at: '2026-09-11T10:00:09Z',
        detail: 'RuntimeError: 0 embedded, 4 failed',
      },
    })

    await screen.findByRole('heading', { name: 'Embed' })

    expect(
      within(stageSection('Embed')).getByText('Last run failed: RuntimeError: 0 embedded, 4 failed'),
    ).toBeInTheDocument()
    expect(within(stageSection('Fetch')).queryByText(/Last run/)).not.toBeInTheDocument()
  })

  it('refetches on demand', async () => {
    const { getPipeline } = renderPage()
    await screen.findByRole('heading', { name: 'Fetch' })

    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))

    expect(getPipeline).toHaveBeenCalledTimes(2)
  })
})
