import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import type { SetDefinition, SetRecord } from '../api/types'
import { Button } from '../components/Button'
import { DeleteRowButton } from '../components/DeleteRowButton'
import { DocumentLink } from '../components/DocumentLink'
import { Link } from '../components/Link'
import { StatusBadge } from '../components/StatusBadge'
import type { SchemaColumn } from '../utils/schema'
import { formatCellValue, schemaColumns } from '../utils/schema'

interface SetsPageProps {
  client: ApiClient
  onUnauthorized: () => void
  /** The tab from the URL (`/sets/:name`), or null for the bare `/sets` default. */
  activeSet: string | null
  onSelectSet: (name: string) => void
}

type SetsState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'empty' }
  | { status: 'ready'; sets: SetDefinition[] }

export function SetsPage({ client, onUnauthorized, activeSet, onSelectSet }: SetsPageProps) {
  const [state, setState] = useState<SetsState>({ status: 'loading' })

  useEffect(() => {
    client
      .listSets()
      .then((sets) => {
        setState(sets.length === 0 ? { status: 'empty' } : { status: 'ready', sets })
      })
      .catch((error: unknown) => {
        if (isUnauthorized(error)) {
          onUnauthorized()
          return
        }
        setState({ status: 'error', message: describeError(error) })
      })
  }, [client, onUnauthorized])

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">Sets</h1>
      {state.status === 'loading' && <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">Loading sets…</p>}
      {state.status === 'error' && (
        <p role="alert" className="mt-6 text-sm text-red-600 dark:text-red-400">
          {state.message}
        </p>
      )}
      {state.status === 'empty' && (
        <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">
          No sets defined yet. Once one exists, its records show up here as a table derived from
          its own schema.
        </p>
      )}
      {state.status === 'ready' && (
        <>
          <SetsTabs sets={state.sets} active={activeSet ?? state.sets[0].name} onSelect={onSelectSet} />
          <SetTable
            key={activeSet ?? state.sets[0].name}
            client={client}
            set={state.sets.find((set) => set.name === activeSet) ?? state.sets[0]}
            onUnauthorized={onUnauthorized}
          />
        </>
      )}
    </div>
  )
}

function SetsTabs({
  sets,
  active,
  onSelect,
}: {
  sets: SetDefinition[]
  active: string
  onSelect: (name: string) => void
}) {
  return (
    <div className="mt-6 flex flex-wrap gap-1 border-b border-zinc-200 dark:border-zinc-800">
      {sets.map((set) => (
        <Link
          key={set.name}
          href={`/sets/${encodeURIComponent(set.name)}`}
          onNavigate={() => { onSelect(set.name) }}
          aria-current={set.name === active ? 'page' : undefined}
          className={`-mb-px rounded-t-md border-b-2 px-3 py-2 text-sm transition-colors duration-150 ${
            set.name === active
              ? 'border-emerald-600 font-medium text-zinc-900 dark:text-zinc-50'
              : 'border-transparent text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200'
          }`}
        >
          {set.name}
        </Link>
      ))}
    </div>
  )
}

type RecordsState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; records: SetRecord[]; hasMore: boolean }

type SortState = { key: string; direction: 'asc' | 'desc' } | null

const PAGE_SIZE = 50
const STATUS_OPTIONS = ['', 'pending', 'ok', 'partial', 'failed'] as const

function SetTable({ client, set, onUnauthorized }: { client: ApiClient; set: SetDefinition; onUnauthorized: () => void }) {
  const [statusFilter, setStatusFilter] = useState('')
  const [state, setState] = useState<RecordsState>({ status: 'loading' })
  const [sort, setSort] = useState<SortState>(null)
  const columns = useMemo(() => schemaColumns(set.schema), [set.schema])

  const fetchPage = useCallback(
    (offset: number, append: boolean) => {
      if (!append) setState({ status: 'loading' })
      client
        .listSetRecords(set.name, { status: statusFilter === '' ? undefined : statusFilter, limit: PAGE_SIZE, offset })
        .then((records) => {
          setState((prev) => ({
            status: 'ready',
            records: append && prev.status === 'ready' ? [...prev.records, ...records] : records,
            hasMore: records.length === PAGE_SIZE,
          }))
        })
        .catch((error: unknown) => {
          if (isUnauthorized(error)) {
            onUnauthorized()
            return
          }
          setState({ status: 'error', message: describeError(error) })
        })
    },
    [client, set.name, statusFilter, onUnauthorized],
  )

  useEffect(() => {
    fetchPage(0, false)
  }, [fetchPage])

  function toggleSort(key: string) {
    setSort((current) =>
      current?.key === key ? { key, direction: current.direction === 'asc' ? 'desc' : 'asc' } : { key, direction: 'asc' },
    )
  }

  function handleDeleted(documentId: number) {
    setState((prev) =>
      prev.status === 'ready'
        ? { ...prev, records: prev.records.filter((record) => record.document_id !== documentId) }
        : prev,
    )
  }

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-zinc-500 dark:text-zinc-400">{set.description}</p>
        <label className="flex shrink-0 items-center gap-2 text-sm text-zinc-600 dark:text-zinc-300">
          Status
          <select
            id="set-status-filter"
            name="set-status-filter"
            value={statusFilter}
            onChange={(event) => { setStatusFilter(event.target.value) }}
            className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === '' ? 'All' : option}
              </option>
            ))}
          </select>
        </label>
      </div>
      <SetRecordsView
        state={state}
        set={set}
        statusFilter={statusFilter}
        columns={columns}
        sort={sort}
        onSort={toggleSort}
        client={client}
        onUnauthorized={onUnauthorized}
        onDeleted={handleDeleted}
        onLoadMore={(offset) => { fetchPage(offset, true) }}
      />
    </div>
  )
}

interface SetRecordsViewProps {
  state: RecordsState
  set: SetDefinition
  statusFilter: string
  columns: SchemaColumn[]
  sort: SortState
  onSort: (key: string) => void
  client: ApiClient
  onUnauthorized: () => void
  onDeleted: (documentId: number) => void
  onLoadMore: (offset: number) => void
}

function SetRecordsView(props: SetRecordsViewProps) {
  const { state, set, statusFilter, columns, sort, onSort, client, onUnauthorized, onDeleted, onLoadMore } = props

  if (state.status === 'loading') {
    return <p className="mt-4 text-sm text-zinc-500 dark:text-zinc-400">Loading records…</p>
  }
  if (state.status === 'error') {
    return (
      <p role="alert" className="mt-4 text-sm text-red-600 dark:text-red-400">
        {state.message}
      </p>
    )
  }

  const records = sortRecords(state.records, sort)
  if (records.length === 0) {
    return (
      <p className="mt-4 text-sm text-zinc-500 dark:text-zinc-400">
        No {statusFilter === '' ? '' : `${statusFilter} `}records in {set.name} yet. Most documents in the garden
        match no set at all, so an empty table here doesn't mean anything is broken.
      </p>
    )
  }

  return (
    <>
      <div className="mt-4 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-zinc-200 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
            <tr>
              <SortableHeader label="Title" sortKey="title" sort={sort} onSort={onSort} />
              <SortableHeader label="Status" sortKey="status" sort={sort} onSort={onSort} />
              {columns.map((column) => (
                <SortableHeader key={column.key} label={column.label} sortKey={column.key} sort={sort} onSort={onSort} />
              ))}
              <th className="px-3 py-2 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {records.map((record) => (
              <RecordRow
                key={record.document_id}
                record={record}
                columns={columns}
                client={client}
                onUnauthorized={onUnauthorized}
                onDeleted={onDeleted}
              />
            ))}
          </tbody>
        </table>
      </div>
      {state.hasMore && (
        <Button variant="ghost" size="sm" className="mt-3" onClick={() => { onLoadMore(state.records.length) }}>
          Load more
        </Button>
      )}
    </>
  )
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string
  sortKey: string
  sort: SortState
  onSort: (key: string) => void
}) {
  const active = sort?.key === sortKey
  return (
    <th className="px-3 py-2 font-medium">
      <button
        type="button"
        onClick={() => { onSort(sortKey) }}
        className="flex items-center gap-1 whitespace-nowrap hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        {label}
        {active && <span aria-hidden="true">{sort.direction === 'asc' ? '↑' : '↓'}</span>}
      </button>
    </th>
  )
}

function RecordRow({
  record,
  columns,
  client,
  onUnauthorized,
  onDeleted,
}: {
  record: SetRecord
  columns: SchemaColumn[]
  client: ApiClient
  onUnauthorized: () => void
  onDeleted: (documentId: number) => void
}) {
  return (
    <tr>
      <td className="max-w-xs truncate px-3 py-2 align-top">
        <DocumentLink title={record.title} url={record.url} />
      </td>
      <td className="px-3 py-2 align-top">
        <StatusBadge status={record.status} />
      </td>
      {columns.map((column) => {
        const missing = record.missing_fields.includes(column.key)
        const value = missing ? 'Missing' : formatCellValue(record.extracted_json?.[column.key])
        return (
          <td
            key={column.key}
            className={`px-3 py-2 align-top ${missing ? 'text-amber-600 italic dark:text-amber-400' : 'text-zinc-700 dark:text-zinc-300'}`}
          >
            {/* Array-typed values (e.g. a `steps` list) can run to dozens of sentences. Capping
             * height here, not on the row, stops one long cell from pushing every sibling cell's
             * short content far below the viewport (a row can only be as tall as its tallest
             * cell forces it to be). */}
            {column.type === 'array' ? (
              <div className="max-h-24 overflow-y-auto">{value}</div>
            ) : (
              value
            )}
          </td>
        )
      })}
      <td className="px-3 py-2 text-right align-top">
        <DeleteRowButton
          client={client}
          documentId={record.document_id}
          onDeleted={() => { onDeleted(record.document_id) }}
          onUnauthorized={onUnauthorized}
        />
      </td>
    </tr>
  )
}

function compareValues(a: unknown, b: unknown): number {
  if (a === b) return 0
  if (a === null || a === undefined) return 1
  if (b === null || b === undefined) return -1
  if (typeof a === 'number' && typeof b === 'number') return a - b
  return formatCellValue(a).localeCompare(formatCellValue(b))
}

function sortValue(record: SetRecord, key: string): unknown {
  if (key === 'title') return record.title
  if (key === 'status') return record.status
  return record.extracted_json?.[key]
}

function sortRecords(records: SetRecord[], sort: SortState): SetRecord[] {
  if (sort === null) return records
  const factor = sort.direction === 'asc' ? 1 : -1
  return [...records].sort((a, b) => factor * compareValues(sortValue(a, sort.key), sortValue(b, sort.key)))
}
