// Mirrors the Pydantic response models in src/links_garden/api.py field-for-field, snake_case
// included: keeping the wire shape avoids a translation layer that could silently drift from the
// API. Keep the two in sync by hand; there is no shared schema generation between them.

export interface Hit {
  document_id: number
  title: string | null
  url: string | null
  source: string
  snippet: string
  score: number
  fts_rank: number | null
  vector_rank: number | null
}

export interface Document {
  id: number
  source: string
  source_ref: string
  url: string | null
  parent_document_id: number | null
  title: string | null
  author: string | null
  content: string | null
  summary: string | null
  keywords: string | null
  message_text: string | null
  status: string
  error: string | null
  fetched_at: string | null
  created_at: string
  updated_at: string
}

// The set's own JSON Schema, kept loosely typed: task 2 reads `properties` off it to derive
// table columns without this client hard-coding a set's shape.
export type JsonSchema = Record<string, unknown>

export interface SetDefinition {
  name: string
  description: string
  schema: JsonSchema
}

export interface SetDeleteResult {
  status: string
  name: string
  records_removed: number
}

export interface SetRecord {
  document_id: number
  title: string | null
  url: string | null
  status: string
  extracted_json: Record<string, unknown> | null
  missing_fields: string[]
}

export interface ReviewItem {
  document_id: number
  title: string | null
  url: string | null
  set_name: string
  status: string
  missing_fields: string[]
  error: string | null
}

export interface DocumentListItem {
  id: number
  source: string
  source_ref: string
  url: string | null
  title: string | null
  author: string | null
  status: string
  error: string | null
  fetched_at: string | null
  created_at: string
  updated_at: string
  embedded: boolean
  enriched: boolean
  set_names: string[]
}

export interface DocumentListPage {
  items: DocumentListItem[]
  next_cursor: string | null
}

/** Enough of a `DocumentListItem` to root the graph on it: a title/url to render before any
 * fetch, and `embedded` to tell "no embeddings yet" apart from "embedded, no neighbours" without
 * a second round trip. */
export type GraphAnchor = Pick<DocumentListItem, 'id' | 'title' | 'url' | 'embedded'>

export type IngestSource = 'manual' | 'mcp'

export interface IngestResult {
  document_id: number | null
  url: string
  status: string
  title: string | null
  error: string | null
}
