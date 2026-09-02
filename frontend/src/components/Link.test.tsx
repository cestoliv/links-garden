import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Link } from './Link'

describe('Link', () => {
  it('renders a real anchor with the given href', () => {
    render(
      <Link href="/documents/42" onNavigate={vi.fn()}>
        Doc 42
      </Link>,
    )

    const anchor = screen.getByRole('link', { name: 'Doc 42' })
    expect(anchor.tagName).toBe('A')
    expect(anchor).toHaveAttribute('href', '/documents/42')
  })

  it('intercepts a plain left-click and hands it to the router instead of the browser', async () => {
    const onNavigate = vi.fn()
    render(
      <Link href="/documents/42" onNavigate={onNavigate}>
        Doc 42
      </Link>,
    )

    await userEvent.click(screen.getByRole('link', { name: 'Doc 42' }))

    expect(onNavigate).toHaveBeenCalledTimes(1)
  })

  it('lets a modified click fall through untouched, for opening in a new tab', async () => {
    const onNavigate = vi.fn()
    render(
      <Link href="/documents/42" onNavigate={onNavigate}>
        Doc 42
      </Link>,
    )
    const user = userEvent.setup()

    await user.keyboard('{Control>}')
    await user.click(screen.getByRole('link', { name: 'Doc 42' }))
    await user.keyboard('{/Control}')

    expect(onNavigate).not.toHaveBeenCalled()
  })
})
