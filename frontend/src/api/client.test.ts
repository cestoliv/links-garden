import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApiClient, isUnauthorized } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('createApiClient', () => {
  it('attaches the bearer token to every request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const client = createApiClient('http://api.test', 'secret-token')
    await client.search('tiktok')

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(init.headers)
    expect(headers.get('Authorization')).toBe('Bearer secret-token')
  })

  it('surfaces a 401 as an error isUnauthorized recognizes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'unauthorized' }), { status: 401 }),
      ),
    )

    const client = createApiClient('http://api.test', 'bad-token')
    let caught: unknown
    try {
      await client.search('tiktok')
    } catch (error) {
      caught = error
    }
    expect(isUnauthorized(caught)).toBe(true)
  })
})
