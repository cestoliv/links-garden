import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApiClient, isUnauthorized } from './client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('createApiClient', () => {
  it('sends every request under the /api prefix', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const client = createApiClient('http://api.test')
    await client.search('tiktok')

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('http://api.test/api/search?q=tiktok')
  })

  it('surfaces a 401 as an error isUnauthorized recognizes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'unauthorized' }), { status: 401 })))

    const client = createApiClient('http://api.test')
    let caught: unknown
    try {
      await client.search('tiktok')
    } catch (error) {
      caught = error
    }
    expect(isUnauthorized(caught)).toBe(true)
  })
})
