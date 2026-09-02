import { useState } from 'react'
import type { ApiClient } from '../api/client'
import { describeError, isUnauthorized } from '../api/client'
import { Button } from './Button'

interface DeleteRowButtonProps {
  client: ApiClient
  documentId: number
  onDeleted: () => void
  onUnauthorized: () => void
}

type DeleteState =
  | { status: 'idle' }
  | { status: 'confirming' }
  | { status: 'deleting' }
  | { status: 'error'; message: string }

const DANGER_GHOST = 'text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40'

// `DELETE /documents/{id}` tombstones rather than hard-deleting, so the confirm copy says so:
// the row is gone for good from this dashboard's next sync, not just from this screen.
export function DeleteRowButton({ client, documentId, onDeleted, onUnauthorized }: DeleteRowButtonProps) {
  const [state, setState] = useState<DeleteState>({ status: 'idle' })

  function handleConfirm() {
    setState({ status: 'deleting' })
    client
      .deleteDocument(documentId)
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
    return (
      <div className="flex items-center gap-2 text-xs whitespace-nowrap">
        <span className="text-zinc-500 dark:text-zinc-400">Delete this document? It won't reappear on the next sync.</span>
        <Button
          variant="ghost"
          size="sm"
          className={DANGER_GHOST}
          disabled={state.status === 'deleting'}
          onClick={handleConfirm}
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
      <Button variant="ghost" size="sm" className={DANGER_GHOST} onClick={() => { setState({ status: 'confirming' }) }}>
        Delete
      </Button>
      {state.status === 'error' && (
        <span role="alert" className="text-xs text-red-600 dark:text-red-400">
          {state.message}
        </span>
      )}
    </div>
  )
}
