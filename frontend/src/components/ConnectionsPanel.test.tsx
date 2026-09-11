import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { makeClient } from '../test/mockClient'
import { ConnectionsPanel } from './ConnectionsPanel'

function renderPanel(overrides: Parameters<typeof makeClient>[0] = {}) {
  const onUnauthorized = vi.fn()
  render(
    <ConnectionsPanel client={makeClient(overrides)} onUnauthorized={onUnauthorized} />,
  )
  return { onUnauthorized }
}

describe('ConnectionsPanel', () => {
  it('pings nothing until asked', () => {
    const pingFirecrawl = vi.fn()
    const pingOllama = vi.fn()
    renderPanel({ pingFirecrawl, pingOllama })

    expect(pingFirecrawl).not.toHaveBeenCalled()
    expect(pingOllama).not.toHaveBeenCalled()
    expect(screen.getAllByText('Not checked yet.')).toHaveLength(2)
  })

  it('reports the Firecrawl budget after a ping', async () => {
    const pingFirecrawl = vi
      .fn()
      .mockResolvedValue({ name: 'firecrawl', ok: true, detail: '1819 credits remaining' })
    renderPanel({ pingFirecrawl })

    await userEvent.click(screen.getAllByRole('button', { name: 'Ping' })[0])

    expect(await screen.findByRole('status')).toHaveTextContent('Reachable: 1819 credits remaining')
    expect(pingFirecrawl).toHaveBeenCalledTimes(1)
  })

  it('reports an unreachable ollama as a problem, not as a broken request', async () => {
    const pingOllama = vi
      .fn()
      .mockResolvedValue({ name: 'ollama', ok: false, detail: 'could not reach ollama' })
    renderPanel({ pingOllama })

    await userEvent.click(screen.getAllByRole('button', { name: 'Ping' })[1])

    expect(await screen.findByRole('status')).toHaveTextContent('Problem: could not reach ollama')
  })

  it('sends the prompt and shows the reply with its timing', async () => {
    const generateWithOllama = vi.fn().mockResolvedValue({ reply: 'Hello to you', elapsed_ms: 2400 })
    renderPanel({ generateWithOllama })

    await userEvent.clear(screen.getByLabelText('Test prompt'))
    await userEvent.type(screen.getByLabelText('Test prompt'), 'say hi')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(generateWithOllama).toHaveBeenCalledWith('say hi')
    expect(await screen.findByText('Hello to you')).toBeInTheDocument()
    expect(screen.getByText('Answered in 2.4s')).toBeInTheDocument()
  })

  it('shows the API detail when a generation fails', async () => {
    const generateWithOllama = vi
      .fn()
      .mockRejectedValue(new ApiError(502, 'could not generate with ollama'))
    renderPanel({ generateWithOllama })

    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('could not generate with ollama')
  })

  it('reports a 401 to the session handler instead of rendering it', async () => {
    const pingFirecrawl = vi.fn().mockRejectedValue(new ApiError(401, 'unauthorized'))
    const { onUnauthorized } = renderPanel({ pingFirecrawl })

    await userEvent.click(screen.getAllByRole('button', { name: 'Ping' })[0])

    expect(onUnauthorized).toHaveBeenCalled()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
