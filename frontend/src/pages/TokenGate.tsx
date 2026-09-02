import { motion, useReducedMotion } from 'motion/react'
import { useState } from 'react'
import { Button } from '../components/Button'

interface TokenGateProps {
  error: string | null
  busy: boolean
  onSubmit: (token: string) => void
}

// Shown whenever there is no verified token in memory: on first load, and again after any 401,
// so a rejected or expired token never leaves the user staring at a blank app.
export function TokenGate({ error, busy, onSubmit }: TokenGateProps) {
  const [value, setValue] = useState('')
  const reduceMotion = useReducedMotion()

  // Typed by inference from the `form`'s onSubmit prop: `FormEvent` itself is deprecated
  // in this @types/react version.
  function handleSubmit(event: { preventDefault: () => void }) {
    event.preventDefault()
    const trimmed = value.trim()
    if (trimmed) onSubmit(trimmed)
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <motion.div
        initial={reduceMotion ? false : { opacity: 0, y: 8, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.25, ease: [0.23, 1, 0.32, 1] }}
        className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
      >
        <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">Links Garden</h1>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          Enter the API token to connect. It stays in memory for this session only, never in
          local storage.
        </p>
        <form onSubmit={handleSubmit} className="mt-4 flex flex-col gap-2" noValidate>
          <label
            htmlFor="api-token"
            className="text-sm font-medium text-zinc-700 dark:text-zinc-300"
          >
            API token
          </label>
          <input
            id="api-token"
            type="password"
            autoComplete="off"
            autoFocus
            value={value}
            disabled={busy}
            onChange={(event) => {
              setValue(event.target.value)
            }}
            className="rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none focus:border-emerald-600 focus:ring-2 focus:ring-emerald-600/30 disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
          />
          {error !== null && (
            <p role="alert" className="text-sm text-red-600 dark:text-red-400">
              {error}
            </p>
          )}
          <Button type="submit" disabled={busy || value.trim() === ''} className="mt-2">
            {busy ? 'Checking…' : 'Connect'}
          </Button>
        </form>
      </motion.div>
    </div>
  )
}
