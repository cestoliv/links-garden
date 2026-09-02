import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { SetDefinition, SetRecord } from '../api/types'
import { makeClient } from '../test/mockClient'
import { SetsPage } from './SetsPage'

// `made_up_set` never appears anywhere else in the codebase; the test only passes if the table's
// columns come from the schema below, not from a hard-coded list of known field names.
const madeUpSet: SetDefinition = {
  name: 'made_up_set',
  description: 'A set invented for this test.',
  schema: {
    type: 'object',
    properties: {
      favorite_snack: { type: 'string' },
      spice_level: { type: ['integer', 'null'] },
    },
  },
}

const madeUpRecord: SetRecord = {
  document_id: 1,
  title: 'Snack notes',
  url: null,
  status: 'ok',
  extracted_json: { favorite_snack: 'pretzels', spice_level: 3 },
  missing_fields: [],
}

describe('SetsPage', () => {
  it('derives table columns from the set schema instead of a hard-coded list', async () => {
    const client = makeClient({
      listSets: vi.fn().mockResolvedValue([madeUpSet]),
      listSetRecords: vi.fn().mockResolvedValue([madeUpRecord]),
    })

    render(<SetsPage client={client} onUnauthorized={vi.fn()} activeSet={null} onSelectSet={vi.fn()} />)

    await waitFor(() => { expect(screen.getByText('Favorite Snack')).toBeInTheDocument() })
    expect(screen.getByText('Spice Level')).toBeInTheDocument()
    expect(screen.getByText('pretzels')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('shows an informative empty state instead of looking broken', async () => {
    const client = makeClient({
      listSets: vi.fn().mockResolvedValue([madeUpSet]),
      listSetRecords: vi.fn().mockResolvedValue([]),
    })

    render(<SetsPage client={client} onUnauthorized={vi.fn()} activeSet={null} onSelectSet={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText(/No records in made_up_set yet/)).toBeInTheDocument()
    })
  })

  // Regression test for the Critical layout bug: `vertical-align: middle` on every `<td>` let a
  // long array cell blow up the row height and center every sibling cell's short content far
  // outside the viewport, with no visual cue anything was there.
  it('caps a long array cell instead of letting it drive the row height, and top-aligns every cell', async () => {
    const recipeSet: SetDefinition = {
      name: 'recipe',
      description: 'A recipe set.',
      schema: { type: 'object', properties: { steps: { type: 'array' } } },
    }
    const record: SetRecord = {
      document_id: 1,
      title: 'Pain',
      url: null,
      status: 'ok',
      extracted_json: { steps: Array.from({ length: 11 }, (_, i) => `Step ${String(i + 1)}.`) },
      missing_fields: [],
    }
    const client = makeClient({
      listSets: vi.fn().mockResolvedValue([recipeSet]),
      listSetRecords: vi.fn().mockResolvedValue([record]),
    })

    render(<SetsPage client={client} onUnauthorized={vi.fn()} activeSet={null} onSelectSet={vi.fn()} />)

    const stepsCell = await screen.findByText(/Step 1\./)
    const scrollBox = stepsCell.closest('div')
    expect(scrollBox?.className).toContain('max-h-24')
    expect(scrollBox?.className).toContain('overflow-y-auto')

    for (const cell of document.querySelectorAll('tbody td')) {
      expect(cell.className).toContain('align-top')
    }
  })
})
