import { useCallback, useEffect, useState } from 'react'
import type { SubmitEvent } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { SetDefinition } from '../api/types'
import { Button } from '../components/Button'

interface SetAdminPageProps {
  client: ApiClient
  onUnauthorized: () => void
}

type ListState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; sets: SetDefinition[] }

type FormMode = { kind: 'closed' } | { kind: 'create' } | { kind: 'edit'; set: SetDefinition }

const DEFAULT_SCHEMA = {
  type: 'object',
  properties: {
    example_field: { type: 'string' },
  },
}

const DANGER_GHOST = 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40'

const INPUT_CLASS =
  'rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-sm text-zinc-900 outline-none ' +
  'focus:border-emerald-600 focus:ring-2 focus:ring-emerald-600/30 disabled:bg-zinc-100 ' +
  'dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:disabled:bg-zinc-800'

export function SetAdminPage({ client, onUnauthorized }: SetAdminPageProps) {
  const [state, setState] = useState<ListState>({ status: 'loading' })
  const [mode, setMode] = useState<FormMode>({ kind: 'closed' })

  const load = useCallback(() => {
    setState({ status: 'loading' })
    client
      .listSets()
      .then((sets) => {
        setState({ status: 'ready', sets })
      })
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setState({ status: 'error', message: describeError(error) })
      })
  }, [client, onUnauthorized])

  useEffect(load, [load])

  function handleSaved() {
    setMode({ kind: 'closed' })
    load()
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Set admin</h1>
        {mode.kind === 'closed' && (
          <Button size="sm" onClick={() => { setMode({ kind: 'create' }) }}>
            New set
          </Button>
        )}
      </div>
      <div className="mt-6">
        {mode.kind !== 'closed' ? (
          <SetForm
            client={client}
            onUnauthorized={onUnauthorized}
            existing={mode.kind === 'edit' ? mode.set : null}
            onSaved={handleSaved}
            onCancel={() => { setMode({ kind: 'closed' }) }}
          />
        ) : (
          <SetListView
            state={state}
            client={client}
            onUnauthorized={onUnauthorized}
            onEdit={(set) => { setMode({ kind: 'edit', set }) }}
            onDeleted={load}
          />
        )}
      </div>
    </div>
  )
}

interface SetListViewProps {
  state: ListState
  client: ApiClient
  onUnauthorized: () => void
  onEdit: (set: SetDefinition) => void
  onDeleted: () => void
}

function SetListView({ state, client, onUnauthorized, onEdit, onDeleted }: SetListViewProps) {
  if (state.status === 'loading') {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">Loading sets…</p>
  }
  if (state.status === 'error') {
    return (
      <p role="alert" className="text-sm text-red-600 dark:text-red-400">
        {state.message}
      </p>
    )
  }
  if (state.sets.length === 0) {
    return (
      <p className="text-sm text-zinc-500 dark:text-zinc-400">
        No sets yet. Create one to start classifying documents.
      </p>
    )
  }
  return (
    <ul className="flex flex-col divide-y divide-zinc-200 dark:divide-zinc-800">
      {state.sets.map((set) => (
        <li key={set.name} className="flex items-start justify-between gap-4 py-3">
          <div>
            <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100">{set.name}</p>
            <p className="text-sm text-zinc-500 dark:text-zinc-400">{set.description}</p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => { onEdit(set) }}>
              Edit
            </Button>
            <DeleteSetButton client={client} set={set} onUnauthorized={onUnauthorized} onDeleted={onDeleted} />
          </div>
        </li>
      ))}
    </ul>
  )
}

interface DeleteSetButtonProps {
  client: ApiClient
  set: SetDefinition
  onUnauthorized: () => void
  onDeleted: () => void
}

type DeleteState =
  | { status: 'idle' }
  | { status: 'counting' }
  | { status: 'confirming'; count: number }
  | { status: 'deleting'; count: number }
  | { status: 'error'; message: string }

// Counts records before deleting, not after: `DELETE /sets/{name}` only reports
// `records_removed` once the cascade already happened, too late to warn with it.
function DeleteSetButton({ client, set, onUnauthorized, onDeleted }: DeleteSetButtonProps) {
  const [state, setState] = useState<DeleteState>({ status: 'idle' })

  function startConfirm() {
    setState({ status: 'counting' })
    client
      .listSetRecords(set.name, { limit: 1000 })
      .then((records) => {
        setState({ status: 'confirming', count: records.length })
      })
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setState({ status: 'error', message: describeError(error) })
      })
  }

  function handleConfirm(count: number) {
    setState({ status: 'deleting', count })
    client
      .deleteSet(set.name)
      .then(onDeleted)
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setState({ status: 'error', message: describeError(error) })
      })
  }

  if (state.status === 'confirming' || state.status === 'deleting') {
    const recordCopy =
      state.count === 0
        ? 'has no extracted records yet'
        : `will lose ${String(state.count)} extracted record${state.count === 1 ? '' : 's'}`
    return (
      <div className="flex items-center gap-2 text-xs whitespace-nowrap">
        <span className="text-zinc-500 dark:text-zinc-400">
          Delete “{set.name}”? It {recordCopy}.
        </span>
        <Button
          variant="ghost"
          size="sm"
          className={DANGER_GHOST}
          disabled={state.status === 'deleting'}
          onClick={() => { handleConfirm(state.count) }}
        >
          {state.status === 'deleting' ? 'Deleting…' : 'Confirm'}
        </Button>
        <Button variant="ghost" size="sm" onClick={() => { setState({ status: 'idle' }) }}>
          Cancel
        </Button>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant="ghost"
        size="sm"
        className={DANGER_GHOST}
        disabled={state.status === 'counting'}
        onClick={startConfirm}
      >
        {state.status === 'counting' ? 'Checking…' : 'Delete'}
      </Button>
      {state.status === 'error' && (
        <span role="alert" className="text-xs text-red-600 dark:text-red-400">
          {state.message}
        </span>
      )}
    </div>
  )
}

interface SetFormProps {
  client: ApiClient
  onUnauthorized: () => void
  existing: SetDefinition | null
  onSaved: (set: SetDefinition) => void
  onCancel: () => void
}

type SaveState = { status: 'idle' } | { status: 'saving' } | { status: 'error'; message: string }

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

// Mirrors `links_garden/sets.py`'s `_validate`: same three checks, same order, same wording.
// Hand-copied, not generated or cross-checked: Save disables before a request ever fires, so
// a wording change on the server side would go unnoticed here until a user hit it live. If
// `_validate`'s messages change, update these four strings to match.
function validateSchemaText(schemaText: string): string | null {
  let schema: unknown
  try {
    schema = JSON.parse(schemaText)
  } catch {
    return 'schema must be a JSON object'
  }
  if (!isPlainObject(schema)) return 'schema must be a JSON object'
  if (schema.type !== 'object') return 'schema must have "type": "object"'
  const properties = schema.properties
  if (!isPlainObject(properties) || Object.keys(properties).length === 0) {
    return 'schema must have a non-empty "properties" object'
  }
  return null
}

// Mirrors `links_garden/sets.py`'s `_validate` plus `create_set`'s name check, in the same
// order, so a message shown before submit never disagrees with the one the server would send.
function validateSetInput(input: {
  name: string
  description: string
  schemaText: string
  requireName: boolean
}): string | null {
  if (input.requireName && input.name.trim() === '') return 'name must not be empty'
  if (input.description.trim() === '') return 'description must not be empty'
  return validateSchemaText(input.schemaText)
}

function SetForm({ client, onUnauthorized, existing, onSaved, onCancel }: SetFormProps) {
  const [name, setName] = useState(existing?.name ?? '')
  const [description, setDescription] = useState(existing?.description ?? '')
  const [schemaText, setSchemaText] = useState(() => JSON.stringify(existing?.schema ?? DEFAULT_SCHEMA, null, 2))
  const [saveState, setSaveState] = useState<SaveState>({ status: 'idle' })

  const validationError = validateSetInput({ name, description, schemaText, requireName: existing === null })
  const displayError = saveState.status === 'error' ? saveState.message : validationError

  function handleSubmit(event: SubmitEvent) {
    event.preventDefault()
    if (validationError !== null) return
    const schema = JSON.parse(schemaText) as Record<string, unknown>
    setSaveState({ status: 'saving' })
    const request =
      existing === null
        ? client.createSet(name.trim(), description, schema)
        : client.updateSet(existing.name, { description, schema })
    request.then(onSaved).catch((error: unknown) => {
      if (isUnauthorized(error)) {
        onUnauthorized()
        return
      }
      setSaveState({ status: 'error', message: describeError(error) })
    })
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-4 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800"
    >
      <h2 className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
        {existing === null ? 'New set' : `Edit “${existing.name}”`}
      </h2>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-zinc-600 dark:text-zinc-400">Name</span>
        <input
          value={name}
          onChange={(event) => { setName(event.target.value) }}
          disabled={existing !== null}
          className={INPUT_CLASS}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-zinc-600 dark:text-zinc-400">Description</span>
        <textarea
          value={description}
          onChange={(event) => { setDescription(event.target.value) }}
          rows={2}
          className={INPUT_CLASS}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-zinc-600 dark:text-zinc-400">JSON Schema</span>
        <textarea
          value={schemaText}
          onChange={(event) => { setSchemaText(event.target.value) }}
          rows={10}
          spellCheck={false}
          className={`${INPUT_CLASS} font-mono text-xs`}
        />
      </label>
      {displayError !== null && (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">
          {displayError}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={saveState.status === 'saving' || validationError !== null}>
          {saveState.status === 'saving' ? 'Saving…' : 'Save'}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}
