import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('renders a clear rejected state when the token gets a 401', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'unauthorized' }), { status: 401 }),
      ),
    )

    render(<App />)
    await userEvent.type(screen.getByLabelText('API token'), 'wrong-token')
    await userEvent.click(screen.getByRole('button', { name: /connect/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/token rejected/i)
  })
})
