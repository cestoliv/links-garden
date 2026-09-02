import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { SetDefinition, SetRecord } from '../api/types'
import { makeClient } from '../test/mockClient'
import { SetAdminPage } from './SetAdminPage'

const recipeSet: SetDefinition = {
  name: 'recipe',
  description: 'Recipes clipped from around the web.',
  schema: {
    type: 'object',
    properties: { duration_in_minutes: { type: 'integer' } },
  },
}

function setSchemaText(text: string) {
  const textarea = screen.getByLabelText('JSON Schema')
  fireEvent.change(textarea, { target: { value: text } })
}

describe('SetAdminPage', () => {
  it('lists existing sets with edit and delete actions', async () => {
    const client = makeClient({ listSets: vi.fn().mockResolvedValue([recipeSet]) })

    render(<SetAdminPage client={client} onUnauthorized={vi.fn()} />)

    await waitFor(() => { expect(screen.getByText('recipe')).toBeInTheDocument() })
    expect(screen.getByText('Recipes clipped from around the web.')).toBeInTheDocument()
    expect(screen.getByText('Edit')).toBeInTheDocument()
    expect(screen.getByText('Delete')).toBeInTheDocument()
  })

  it('blocks submit on an invalid schema and shows why', async () => {
    const createSet = vi.fn()
    const client = makeClient({ listSets: vi.fn().mockResolvedValue([]), createSet })

    render(<SetAdminPage client={client} onUnauthorized={vi.fn()} />)

    await waitFor(() => { expect(screen.getByText('No sets yet. Create one to start classifying documents.')).toBeInTheDocument() })
    await userEvent.click(screen.getByText('New set'))

    await userEvent.type(screen.getByLabelText('Name'), 'movie')
    await userEvent.type(screen.getByLabelText('Description'), 'Films worth rewatching.')
    setSchemaText('{"type": "object", "properties": {}}')

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('schema must have a non-empty "properties" object')
    })
    expect(screen.getByText('Save')).toBeDisabled()
    expect(createSet).not.toHaveBeenCalled()
  })

  it('creates a set with a valid schema and returns to the list', async () => {
    const createdSet: SetDefinition = { ...recipeSet, name: 'movie' }
    const createSet = vi.fn().mockResolvedValue(createdSet)
    const listSets = vi.fn().mockResolvedValueOnce([]).mockResolvedValueOnce([createdSet])
    const client = makeClient({ listSets, createSet })

    render(<SetAdminPage client={client} onUnauthorized={vi.fn()} />)

    await waitFor(() => { expect(screen.getByText('New set')).toBeInTheDocument() })
    await userEvent.click(screen.getByText('New set'))

    await userEvent.type(screen.getByLabelText('Name'), 'movie')
    await userEvent.type(screen.getByLabelText('Description'), 'Films worth rewatching.')

    await userEvent.click(screen.getByText('Save'))

    await waitFor(() => { expect(createSet).toHaveBeenCalledWith('movie', 'Films worth rewatching.', expect.any(Object)) })
    await waitFor(() => { expect(screen.getByText('New set')).toBeInTheDocument() })
  })

  it('shows the server rejection message verbatim on a duplicate name', async () => {
    const createSet = vi.fn().mockRejectedValue(new ApiError(409, "a set named 'recipe' already exists"))
    const client = makeClient({ listSets: vi.fn().mockResolvedValue([]), createSet })

    render(<SetAdminPage client={client} onUnauthorized={vi.fn()} />)

    await waitFor(() => { expect(screen.getByText('New set')).toBeInTheDocument() })
    await userEvent.click(screen.getByText('New set'))
    await userEvent.type(screen.getByLabelText('Name'), 'recipe')
    await userEvent.type(screen.getByLabelText('Description'), 'Duplicate.')
    await userEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent("a set named 'recipe' already exists")
    })
  })

  it('warns with a concrete record count before deleting', async () => {
    const records: SetRecord[] = [
      { document_id: 1, title: 'a', url: null, status: 'ok', extracted_json: {}, missing_fields: [] },
      { document_id: 2, title: 'b', url: null, status: 'ok', extracted_json: {}, missing_fields: [] },
    ]
    const listSetRecords = vi.fn().mockResolvedValue(records)
    const deleteSet = vi.fn().mockResolvedValue({ status: 'deleted', name: 'recipe', records_removed: 2 })
    const client = makeClient({ listSets: vi.fn().mockResolvedValue([recipeSet]), listSetRecords, deleteSet })

    render(<SetAdminPage client={client} onUnauthorized={vi.fn()} />)

    await waitFor(() => { expect(screen.getByText('recipe')).toBeInTheDocument() })
    await userEvent.click(screen.getByText('Delete'))

    await waitFor(() => { expect(screen.getByText(/will lose 2 extracted records/)).toBeInTheDocument() })
    expect(deleteSet).not.toHaveBeenCalled()

    await userEvent.click(screen.getByText('Confirm'))
    await waitFor(() => { expect(deleteSet).toHaveBeenCalledWith('recipe') })
  })
})
