import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Route } from './useRouter'
import { parseRoute, useRouter } from './useRouter'

afterEach(() => {
  window.history.replaceState(null, '', '/')
})

describe('parseRoute', () => {
  it('parses the bare root as search, with its query from ?q=', () => {
    expect(parseRoute({ pathname: '/', search: '' })).toEqual({ name: 'search', query: '' })
    expect(parseRoute({ pathname: '/', search: '?q=tiktok' })).toEqual({ name: 'search', query: 'tiktok' })
  })

  it('parses /documents and /documents/:id', () => {
    expect(parseRoute({ pathname: '/documents', search: '' })).toEqual({ name: 'documents' })
    expect(parseRoute({ pathname: '/documents/42', search: '' })).toEqual({ name: 'document', id: 42 })
  })

  it('falls back to not-found for a non-numeric document id', () => {
    expect(parseRoute({ pathname: '/documents/abc', search: '' })).toEqual({ name: 'not-found', path: '/documents/abc' })
  })

  it('parses /sets and /sets/:name, percent-decoding the name', () => {
    expect(parseRoute({ pathname: '/sets', search: '' })).toEqual({ name: 'sets', active: null })
    expect(parseRoute({ pathname: '/sets/my%20set', search: '' })).toEqual({ name: 'sets', active: 'my set' })
  })

  it('parses /review and /admin as leaf routes with no sub-path', () => {
    expect(parseRoute({ pathname: '/review', search: '' })).toEqual({ name: 'review' })
    expect(parseRoute({ pathname: '/admin', search: '' })).toEqual({ name: 'admin' })
    expect(parseRoute({ pathname: '/review/extra', search: '' })).toEqual({ name: 'not-found', path: '/review/extra' })
  })

  it('parses /graph and /graph/:id', () => {
    expect(parseRoute({ pathname: '/graph', search: '' })).toEqual({ name: 'graph', anchorId: null })
    expect(parseRoute({ pathname: '/graph/7', search: '' })).toEqual({ name: 'graph', anchorId: 7 })
  })

  it('falls back to not-found for an unknown path', () => {
    expect(parseRoute({ pathname: '/nope', search: '' })).toEqual({ name: 'not-found', path: '/nope' })
  })
})

function RouteProbe() {
  const { route, navigate } = useRouter()
  return (
    <div>
      <p>{JSON.stringify(route)}</p>
      <button type="button" onClick={() => { navigate('/documents/1') }}>
        Go to doc 1
      </button>
      <button type="button" onClick={() => { navigate('/documents/2') }}>
        Go to doc 2
      </button>
    </div>
  )
}

function routeText(route: Route): string {
  return JSON.stringify(route)
}

describe('useRouter', () => {
  it('reads the initial route from the current location', () => {
    window.history.replaceState(null, '', '/documents/1')
    render(<RouteProbe />)

    expect(screen.getByText(routeText({ name: 'document', id: 1 }))).toBeInTheDocument()
  })

  it('updates the route and history on navigate, and responds to Back/Forward', async () => {
    render(<RouteProbe />)
    expect(screen.getByText(routeText({ name: 'search', query: '' }))).toBeInTheDocument()

    act(() => {
      screen.getByText('Go to doc 1').click()
    })
    expect(screen.getByText(routeText({ name: 'document', id: 1 }))).toBeInTheDocument()

    act(() => {
      screen.getByText('Go to doc 2').click()
    })
    expect(screen.getByText(routeText({ name: 'document', id: 2 }))).toBeInTheDocument()

    // jsdom delivers popstate on its own task queue, so Back and Forward need a retrying
    // assertion. A fixed setTimeout(0) races the event and fails intermittently.
    window.history.back()
    expect(await screen.findByText(routeText({ name: 'document', id: 1 }))).toBeInTheDocument()

    window.history.forward()
    expect(await screen.findByText(routeText({ name: 'document', id: 2 }))).toBeInTheDocument()
  })
})
