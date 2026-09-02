// Turns a set's own JSON Schema into table columns and back into typed values, so a set the
// user adds tomorrow renders correctly with no code change here.
import type { JsonSchema } from '../api/types'

export interface SchemaColumn {
  key: string
  label: string
  type: string
}

/** One column per schema property, in the schema's own key order. */
export function schemaColumns(schema: JsonSchema): SchemaColumn[] {
  const properties = schema.properties
  if (typeof properties !== 'object' || properties === null) return []
  return Object.entries(properties as Record<string, unknown>).map(([key, property]) => ({
    key,
    label: humanize(key),
    type: propertyType(property),
  }))
}

// JSON Schema allows `"type": ["integer", "null"]` for a nullable field; the first non-null
// entry is the type that matters for choosing an input and parsing a value back.
function propertyType(property: unknown): string {
  if (typeof property !== 'object' || property === null) return 'string'
  const { type } = property as Record<string, unknown>
  if (typeof type === 'string') return type
  if (Array.isArray(type)) {
    const named = type.find((entry): entry is string => typeof entry === 'string' && entry !== 'null')
    if (named !== undefined) return named
  }
  return 'string'
}

function humanize(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

/** Renders any schema-typed value for a table cell; arrays flatten to one comma-joined line. */
export function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (Array.isArray(value)) return value.map(formatCellValue).join(', ')
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  return JSON.stringify(value)
}

/** Parses a text input back into the type the schema declares, for a PATCH body. */
export function parseCellInput(raw: string, type: string): unknown {
  const trimmed = raw.trim()
  if (type === 'integer' || type === 'number') {
    const parsed = Number(trimmed)
    return Number.isNaN(parsed) ? trimmed : parsed
  }
  if (type === 'boolean') return trimmed === 'true'
  if (type === 'array') {
    return trimmed
      .split(',')
      .map((part) => part.trim())
      .filter((part) => part !== '')
  }
  return trimmed
}
