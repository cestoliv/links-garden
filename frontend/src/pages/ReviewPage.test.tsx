import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ReviewItem, SetDefinition, SetRecord } from '../api/types'
import { makeClient } from '../test/mockClient'
import { ReviewPage } from './ReviewPage'

const recipeSet: SetDefinition = {
  name: 'recipe',
  description: 'Recipes clipped from around the web.',
  schema: {
    type: 'object',
    properties: {
      duration_in_minutes: { type: ['integer', 'null'] },
    },
    required: ['duration_in_minutes'],
  },
}

const reviewItem: ReviewItem = {
  document_id: 5,
  title: 'Weeknight pasta',
  url: null,
  set_name: 'recipe',
  status: 'partial',
  missing_fields: ['duration_in_minutes'],
  error: null,
}

const patchedRecord: SetRecord = {
  document_id: 5,
  title: 'Weeknight pasta',
  url: null,
  status: 'ok',
  extracted_json: { duration_in_minutes: 90 },
  missing_fields: [],
}

describe('ReviewPage', () => {
  it('renders the missing field name', async () => {
    const client = makeClient({
      listReview: vi.fn().mockResolvedValue([reviewItem]),
      listSets: vi.fn().mockResolvedValue([recipeSet]),
    })

    render(<ReviewPage client={client} onUnauthorized={vi.fn()} />)

    await waitFor(() => { expect(screen.getByText('duration_in_minutes')).toBeInTheDocument() })
  })

  it('patches only the field the user filled in', async () => {
    const patchRecord = vi.fn().mockResolvedValue(patchedRecord)
    const client = makeClient({
      listReview: vi.fn().mockResolvedValue([reviewItem]),
      listSets: vi.fn().mockResolvedValue([recipeSet]),
      patchRecord,
    })

    render(<ReviewPage client={client} onUnauthorized={vi.fn()} />)

    await waitFor(() => { expect(screen.getByText('Fill missing fields')).toBeInTheDocument() })
    await userEvent.click(screen.getByText('Fill missing fields'))
    await userEvent.type(screen.getByLabelText('Duration In Minutes'), '90')
    await userEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(patchRecord).toHaveBeenCalledWith('recipe', 5, { duration_in_minutes: 90 })
    })
  })
})
