import type {
  Document,
  DocumentListPage,
  Hit,
  IngestResult,
  IngestSource,
  JsonSchema,
  ReviewItem,
  SetDefinition,
  SetDeleteResult,
  SetRecord,
} from './types'

/** Thrown for any non-2xx response. `status` lets callers special-case 401 without string matching. */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
  }
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401
}

/** A message safe to show a user directly: the server's own detail, or a guess for network
 * failures, which `fetch` reports as a `TypeError` with no status code to inspect. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof TypeError) return 'Could not reach the API. Is it running?'
  return 'Something went wrong.'
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null && 'detail' in body) {
      if (typeof body.detail === 'string') return body.detail
    }
  } catch {
    // Body wasn't JSON; fall through to the status text below.
  }
  return response.statusText || `request failed with status ${String(response.status)}`
}

export interface ApiClient {
  search: (query: string, limit?: number) => Promise<Hit[]>
  getDocument: (id: number) => Promise<Document>
  getRelated: (id: number, limit?: number) => Promise<Hit[]>
  deleteDocument: (id: number) => Promise<void>
  listDocuments: (options?: { limit?: number; cursor?: string }) => Promise<DocumentListPage>
  listSets: () => Promise<SetDefinition[]>
  getSet: (name: string) => Promise<SetDefinition>
  createSet: (name: string, description: string, schema: JsonSchema) => Promise<SetDefinition>
  updateSet: (
    name: string,
    fields: { description?: string; schema?: JsonSchema },
  ) => Promise<SetDefinition>
  deleteSet: (name: string) => Promise<SetDeleteResult>
  listSetRecords: (
    name: string,
    options?: { status?: string; limit?: number; offset?: number },
  ) => Promise<SetRecord[]>
  patchRecord: (
    name: string,
    documentId: number,
    fields: Record<string, unknown>,
  ) => Promise<SetRecord>
  listReview: (limit?: number) => Promise<ReviewItem[]>
  ingest: (url: string, source?: IngestSource) => Promise<IngestResult>
}

/** A client bound to one base URL. Auth rides on the browser's session cookie, sent
 * automatically on every same-origin request, so there is no token to attach here. */
export function createApiClient(baseUrl: string): ApiClient {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    // Headers merged via the Headers API, not object spread: init.headers can be a plain
    // object, a Headers instance, or an array of tuples, and only Headers handles all three.
    const headers = new Headers(init.headers)
    if (init.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    const response = await fetch(`${baseUrl}/api${path}`, { ...init, headers })
    if (!response.ok) {
      throw new ApiError(response.status, await readErrorDetail(response))
    }
    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  function query(params: Record<string, string | number | undefined>): string {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) search.set(key, String(value))
    }
    const qs = search.toString()
    return qs ? `?${qs}` : ''
  }

  return {
    search: (q, limit) => request(`/search${query({ q, limit })}`),
    getDocument: (id) => request(`/documents/${String(id)}`),
    getRelated: (id, limit) => request(`/documents/${String(id)}/related${query({ limit })}`),
    deleteDocument: (id) => request(`/documents/${String(id)}`, { method: 'DELETE' }),
    listDocuments: (options = {}) => request(`/documents${query(options)}`),
    listSets: () => request('/sets'),
    getSet: (name) => request(`/sets/${encodeURIComponent(name)}`),
    createSet: (name, description, schema) =>
      request('/sets', { method: 'POST', body: JSON.stringify({ name, description, schema }) }),
    updateSet: (name, fields) =>
      request(`/sets/${encodeURIComponent(name)}`, { method: 'PATCH', body: JSON.stringify(fields) }),
    deleteSet: (name) => request(`/sets/${encodeURIComponent(name)}`, { method: 'DELETE' }),
    listSetRecords: (name, options = {}) =>
      request(`/sets/${encodeURIComponent(name)}/records${query(options)}`),
    patchRecord: (name, documentId, fields) =>
      request(`/sets/${encodeURIComponent(name)}/records/${String(documentId)}`, {
        method: 'PATCH',
        body: JSON.stringify(fields),
      }),
    listReview: (limit) => request(`/review${query({ limit })}`),
    ingest: (url, source) =>
      request('/ingest', { method: 'POST', body: JSON.stringify({ url, source }) }),
  }
}

// Session endpoints, called before any `ApiClient` exists: signing in is what makes one usable.

/** Exchanges an API token for a session cookie. Throws `ApiError` (401 for a wrong token) on
 * failure. */
export async function login(baseUrl: string, token: string): Promise<void> {
  const response = await fetch(`${baseUrl}/api/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  })
  if (!response.ok) throw new ApiError(response.status, await readErrorDetail(response))
}

/** True for a still-valid session cookie, false for none/expired (401). Anything else (a network
 * failure, a 5xx) throws, so a real outage isn't reported as "signed out". */
export async function checkSession(baseUrl: string): Promise<boolean> {
  const response = await fetch(`${baseUrl}/api/session`)
  if (response.status === 401) return false
  if (!response.ok) throw new ApiError(response.status, await readErrorDetail(response))
  return true
}

/** Revokes the session server-side and clears the cookie. */
export async function logout(baseUrl: string): Promise<void> {
  const response = await fetch(`${baseUrl}/api/session`, { method: 'DELETE' })
  if (!response.ok) throw new ApiError(response.status, await readErrorDetail(response))
}
