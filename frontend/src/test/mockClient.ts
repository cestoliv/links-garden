import type { ApiClient } from '../api/client'

/** A fully-stubbed client for tests that only exercise a few methods; the rest reject loudly if
 * a test accidentally calls one it didn't mean to. */
export function makeClient(overrides: Partial<ApiClient>): ApiClient {
  const notImplemented = () => Promise.reject(new Error('not used by this test'))
  return {
    search: notImplemented,
    getDocument: notImplemented,
    getRelated: notImplemented,
    deleteDocument: notImplemented,
    listDocuments: notImplemented,
    listSets: notImplemented,
    getSet: notImplemented,
    createSet: notImplemented,
    updateSet: notImplemented,
    deleteSet: notImplemented,
    listSetRecords: notImplemented,
    patchRecord: notImplemented,
    listReview: notImplemented,
    ingest: notImplemented,
    ...overrides,
  }
}
