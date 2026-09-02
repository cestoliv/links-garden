import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeClient } from '../test/mockClient'
import { DeleteRowButton } from './DeleteRowButton'

describe('DeleteRowButton', () => {
  it('asks for confirmation before calling the API, then deletes on confirm', async () => {
    const deleteDocument = vi.fn().mockResolvedValue(undefined)
    const onDeleted = vi.fn()
    const client = makeClient({ deleteDocument })

    render(<DeleteRowButton client={client} documentId={42} onDeleted={onDeleted} onUnauthorized={vi.fn()} />)

    await userEvent.click(screen.getByText('Delete'))
    expect(deleteDocument).not.toHaveBeenCalled()
    expect(screen.getByText(/won.t reappear/i)).toBeInTheDocument()

    await userEvent.click(screen.getByText('Confirm'))
    expect(deleteDocument).toHaveBeenCalledWith(42)
    await vi.waitFor(() => { expect(onDeleted).toHaveBeenCalled() })
  })

  it('never calls the API when the user cancels', async () => {
    const deleteDocument = vi.fn().mockResolvedValue(undefined)
    const client = makeClient({ deleteDocument })

    render(<DeleteRowButton client={client} documentId={42} onDeleted={vi.fn()} onUnauthorized={vi.fn()} />)

    await userEvent.click(screen.getByText('Delete'))
    await userEvent.click(screen.getByText('Cancel'))

    expect(deleteDocument).not.toHaveBeenCalled()
    expect(screen.getByText('Delete')).toBeInTheDocument()
  })
})
