import { useCallback, useEffect, useState } from 'react'
import type { SubmitEvent } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { JsonSchema, ReviewItem, SetRecord } from '../api/types'
import { Button } from '../components/Button'
import { DeleteRowButton } from '../components/DeleteRowButton'
import { DocumentLink } from '../components/DocumentLink'
import { StatusBadge } from '../components/StatusBadge'
import type { SchemaColumn } from '../utils/schema'
import { parseCellInput, schemaColumns } from '../utils/schema'

interface ReviewPageProps {
  client: ApiClient
  onUnauthorized: () => void
}

type ReviewState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; items: ReviewItem[]; schemas: Record<string, JsonSchema> }

const REVIEW_LIMIT = 100

export function ReviewPage({ client, onUnauthorized }: ReviewPageProps) {
  const [state, setState] = useState<ReviewState>({ status: 'loading' })

  const load = useCallback(() => {
    setState({ status: 'loading' })
    Promise.all([client.listReview(REVIEW_LIMIT), client.listSets()])
      .then(([items, sets]) => {
        setState({ status: 'ready', items, schemas: Object.fromEntries(sets.map((set) => [set.name, set.schema])) })
      })
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setState({ status: 'error', message: describeError(error) })
      })
  }, [client, onUnauthorized])

  useEffect(() => {
    load()
  }, [load])

  function removeItem(documentId: number, setName: string) {
    setState((prev) =>
      prev.status === 'ready'
        ? { ...prev, items: prev.items.filter((item) => !(item.document_id === documentId && item.set_name === setName)) }
        : prev,
    )
  }

  // The API recomputed status and missing_fields; a still-partial/failed row gets the fresh
  // values, anything else (now `ok`) drops out of the queue instead of showing a stale row.
  function replaceItem(documentId: number, setName: string, patched: SetRecord) {
    if (patched.status !== 'partial' && patched.status !== 'failed') {
      removeItem(documentId, setName)
      return
    }
    setState((prev) =>
      prev.status === 'ready'
        ? {
            ...prev,
            items: prev.items.map((item) =>
              item.document_id === documentId && item.set_name === setName
                ? { ...item, status: patched.status, missing_fields: patched.missing_fields }
                : item,
            ),
          }
        : prev,
    )
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Review queue</h1>
      <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
        Extractions the classifier couldn't finish. Fill a missing field to move a document out of
        this list.
      </p>
      <ReviewList
        state={state}
        client={client}
        onUnauthorized={onUnauthorized}
        onDeleted={removeItem}
        onPatched={replaceItem}
      />
    </div>
  )
}

interface ReviewListProps {
  state: ReviewState
  client: ApiClient
  onUnauthorized: () => void
  onDeleted: (documentId: number, setName: string) => void
  onPatched: (documentId: number, setName: string, patched: SetRecord) => void
}

function ReviewList({ state, client, onUnauthorized, onDeleted, onPatched }: ReviewListProps) {
  if (state.status === 'loading') {
    return <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">Loading the review queue…</p>
  }
  if (state.status === 'error') {
    return (
      <p role="alert" className="mt-6 text-sm text-red-600 dark:text-red-400">
        {state.message}
      </p>
    )
  }
  if (state.items.length === 0) {
    return (
      <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">
        Nothing to review right now. Every extraction attempted so far is either complete or still
        pending, both of which stay off this list.
      </p>
    )
  }
  return (
    <ul className="mt-6 flex flex-col gap-3">
      {state.items.map((item) => (
        <ReviewRow
          key={`${item.set_name}:${String(item.document_id)}`}
          item={item}
          schema={state.schemas[item.set_name]}
          client={client}
          onUnauthorized={onUnauthorized}
          onDeleted={onDeleted}
          onPatched={onPatched}
        />
      ))}
    </ul>
  )
}

interface ReviewRowProps {
  item: ReviewItem
  schema: JsonSchema | undefined
  client: ApiClient
  onUnauthorized: () => void
  onDeleted: (documentId: number, setName: string) => void
  onPatched: (documentId: number, setName: string, patched: SetRecord) => void
}

function ReviewRow({ item, schema, client, onUnauthorized, onDeleted, onPatched }: ReviewRowProps) {
  const [editing, setEditing] = useState(false)
  const columns = schema === undefined ? [] : schemaColumns(schema)
  const missingColumns = columns.filter((column) => item.missing_fields.includes(column.key))

  return (
    <li className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <DocumentLink title={item.title} url={item.url} />
          <p className="mt-0.5 text-xs tracking-wide text-zinc-400 uppercase dark:text-zinc-500">{item.set_name}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusBadge status={item.status} />
          <DeleteRowButton
            client={client}
            documentId={item.document_id}
            onUnauthorized={onUnauthorized}
            onDeleted={() => { onDeleted(item.document_id, item.set_name) }}
          />
        </div>
      </div>
      {item.error !== null && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{item.error}</p>}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {item.missing_fields.map((field) => (
          <span
            key={field}
            className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-900/50 dark:text-amber-300"
          >
            {field}
          </span>
        ))}
      </div>
      {editing ? (
        <ReviewEditForm
          item={item}
          columns={missingColumns}
          client={client}
          onUnauthorized={onUnauthorized}
          onCancel={() => { setEditing(false) }}
          onSaved={(patched) => {
            onPatched(item.document_id, item.set_name, patched)
            setEditing(false)
          }}
        />
      ) : (
        <button
          type="button"
          onClick={() => { setEditing(true) }}
          disabled={missingColumns.length === 0}
          className="mt-3 text-sm font-medium text-emerald-700 hover:underline disabled:cursor-not-allowed disabled:text-zinc-400 disabled:no-underline dark:text-emerald-400 dark:disabled:text-zinc-600"
        >
          Fill missing fields
        </button>
      )}
    </li>
  )
}

interface ReviewEditFormProps {
  item: ReviewItem
  columns: SchemaColumn[]
  client: ApiClient
  onUnauthorized: () => void
  onCancel: () => void
  onSaved: (patched: SetRecord) => void
}

type SaveState = { status: 'idle' } | { status: 'saving' } | { status: 'error'; message: string }

function ReviewEditForm({ item, columns, client, onUnauthorized, onCancel, onSaved }: ReviewEditFormProps) {
  // Seeded with an empty string per missing field up front, so a lookup below never needs to
  // handle "key not set yet" - only "set but blank".
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(columns.map((column) => [column.key, ''])),
  )
  const [saveState, setSaveState] = useState<SaveState>({ status: 'idle' })

  function handleSubmit(event: SubmitEvent) {
    event.preventDefault()
    const fields: Record<string, unknown> = {}
    for (const column of columns) {
      const raw = values[column.key]
      if (raw.trim() === '') continue
      fields[column.key] = parseCellInput(raw, column.type)
    }
    if (Object.keys(fields).length === 0) {
      onCancel()
      return
    }
    setSaveState({ status: 'saving' })
    client
      .patchRecord(item.set_name, item.document_id, fields)
      .then(onSaved)
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setSaveState({ status: 'error', message: describeError(error) })
      })
  }

  return (
    <form onSubmit={handleSubmit} className="mt-3 flex flex-col gap-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
      {columns.map((column) => (
        <label key={column.key} className="flex flex-col gap-1 text-sm">
          <span className="text-zinc-600 dark:text-zinc-300">{column.label}</span>
          <input
            type={column.type === 'integer' || column.type === 'number' ? 'number' : 'text'}
            value={values[column.key]}
            onChange={(event) => {
              const raw = event.target.value
              setValues((current) => ({ ...current, [column.key]: raw }))
            }}
            className="rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm text-zinc-900 outline-none focus:border-emerald-600 focus:ring-2 focus:ring-emerald-600/30 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
          />
        </label>
      ))}
      {saveState.status === 'error' && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {saveState.message}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={saveState.status === 'saving'}>
          {saveState.status === 'saving' ? 'Saving…' : 'Save'}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
