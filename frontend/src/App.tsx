import { useCallback, useEffect, useMemo, useState } from 'react'
import { checkSession, createApiClient, describeError, isUnauthorized, login, logout } from './api/client'
import { AppShell } from './components/AppShell'
import { API_BASE_URL } from './config'
import { TokenGate } from './pages/TokenGate'

type AuthState =
  | { status: 'checking' }
  | { status: 'signed-out'; error: string | null }
  | { status: 'validating' }
  | { status: 'signed-in' }

function App() {
  const [auth, setAuth] = useState<AuthState>({ status: 'checking' })
  const client = useMemo(() => createApiClient(API_BASE_URL), [])

  // Runs once, before anything else renders: the SPA fallback (Decision 3) already served
  // whatever path the user typed or pasted, and the router below reads it from
  // `window.location` — this check never touches that, so a deep link survives it untouched.
  useEffect(() => {
    checkSession(API_BASE_URL)
      .then((valid) => {
        setAuth(valid ? { status: 'signed-in' } : { status: 'signed-out', error: null })
      })
      .catch((error: unknown) => {
        setAuth({ status: 'signed-out', error: describeError(error) })
      })
  }, [])

  const handleSubmitToken = useCallback((token: string) => {
    setAuth({ status: 'validating' })
    login(API_BASE_URL, token)
      .then(() => {
        setAuth({ status: 'signed-in' })
      })
      .catch((error: unknown) => {
        setAuth({
          status: 'signed-out',
          error: isUnauthorized(error) ? 'Token rejected. Check the value and try again.' : describeError(error),
        })
      })
  }, [])

  const handleUnauthorized = useCallback(() => {
    setAuth({ status: 'signed-out', error: 'Token rejected. Sign in again.' })
  }, [])

  const handleSignOut = useCallback(() => {
    void logout(API_BASE_URL).finally(() => {
      setAuth({ status: 'signed-out', error: null })
    })
  }, [])

  if (auth.status === 'checking') {
    return <div className="flex min-h-dvh items-center justify-center text-sm text-zinc-500 dark:text-zinc-400">Loading…</div>
  }

  if (auth.status !== 'signed-in') {
    return (
      <TokenGate
        error={auth.status === 'signed-out' ? auth.error : null}
        busy={auth.status === 'validating'}
        onSubmit={handleSubmitToken}
      />
    )
  }

  return <AppShell client={client} onUnauthorized={handleUnauthorized} onSignOut={handleSignOut} />
}

export default App
