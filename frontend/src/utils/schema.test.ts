import { describe, expect, it } from 'vitest'
import type { JsonSchema } from '../api/types'
import { formatCellValue, parseCellInput, schemaColumns } from './schema'

describe('schemaColumns', () => {
  it('derives one column per schema property, without any hard-coded field list', () => {
    const schema: JsonSchema = {
      type: 'object',
      properties: {
        a_made_up_field: { type: 'string' },
        another_one: { type: ['integer', 'null'] },
      },
    }

    expect(schemaColumns(schema)).toEqual([
      { key: 'a_made_up_field', label: 'A Made Up Field', type: 'string' },
      { key: 'another_one', label: 'Another One', type: 'integer' },
    ])
  })

  it('returns no columns for a schema with no properties', () => {
    expect(schemaColumns({ type: 'object' })).toEqual([])
  })
})

describe('formatCellValue', () => {
  it('joins arrays with a comma', () => {
    expect(formatCellValue(['a', 'b'])).toBe('a, b')
  })

  it('renders booleans as Yes/No', () => {
    expect(formatCellValue(true)).toBe('Yes')
    expect(formatCellValue(false)).toBe('No')
  })

  it('renders null and undefined as an empty string', () => {
    expect(formatCellValue(null)).toBe('')
    expect(formatCellValue(undefined)).toBe('')
  })
})

describe('parseCellInput', () => {
  it('parses numbers for integer and number types', () => {
    expect(parseCellInput('42', 'integer')).toBe(42)
    expect(parseCellInput('3.5', 'number')).toBe(3.5)
  })

  it('leaves strings alone for other types', () => {
    expect(parseCellInput('hello', 'string')).toBe('hello')
  })
})
