import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => {
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})


// `fetch`'s first argument is `Request | string | URL`; stringifying it directly gives
// '[object Object]' for a Request, so read the URL off whichever shape arrived.
function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  return input.url
}

describe('App', () => {
  it('renders a clear rejected state when the token gets a 401', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'unauthorized' }), { status: 401 }),
      ),
    )

    render(<App />)
    await userEvent.type(await screen.findByLabelText('API token'), 'wrong-token')
    await userEvent.click(screen.getByRole('button', { name: /connect/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/token rejected/i)
  })

  it('lands on the originally requested document after signing in', async () => {
    window.history.replaceState(null, '', '/documents/42')
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/api/session') && method === 'GET') {
        return Promise.resolve(new Response(null, { status: 401 }))
      }
      if (url.endsWith('/api/session') && method === 'POST') {
        return Promise.resolve(new Response(null, { status: 200 }))
      }
      if (url.includes('/api/documents/42')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              id: 42,
              source: 'obsidian',
              source_ref: 'x',
              url: null,
              parent_document_id: null,
              title: 'Doc 42',
              author: null,
              content: 'Body',
              summary: null,
              keywords: null,
              message_text: null,
              status: 'ok',
              error: null,
              fetched_at: null,
              created_at: '2026-01-01T00:00:00Z',
              updated_at: '2026-01-01T00:00:00Z',
              embedded: false,
              enriched: false,
              set_names: [],
            }),
            { status: 200 },
          ),
        )
      }
      if (url.includes('/related')) {
        return Promise.resolve(new Response(JSON.stringify([]), { status: 200 }))
      }
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)
    await userEvent.type(await screen.findByLabelText('API token'), 'right-token')
    await userEvent.click(screen.getByRole('button', { name: /connect/i }))

    expect(await screen.findByText('Doc 42')).toBeInTheDocument()
  })
})
