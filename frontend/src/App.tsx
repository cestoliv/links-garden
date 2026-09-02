import { useCallback, useMemo, useState } from 'react'
import { createApiClient, describeError, isUnauthorized } from './api/client'
import { AppShell } from './components/AppShell'
import { API_BASE_URL } from './config'
import { TokenGate } from './pages/TokenGate'

type AuthState =
  | { status: 'signed-out'; error: string | null }
  | { status: 'validating' }
  | { status: 'signed-in'; token: string }

// The token lives only in this component's state: never localStorage, never sessionStorage,
// both readable by any script on the page. A refresh means entering it again, on purpose.
function App() {
  const [auth, setAuth] = useState<AuthState>({ status: 'signed-out', error: null })

  const token = auth.status === 'signed-in' ? auth.token : null
  const client = useMemo(() => (token !== null ? createApiClient(API_BASE_URL, token) : null), [token])

  const handleSubmitToken = useCallback((candidate: string) => {
    setAuth({ status: 'validating' })
    createApiClient(API_BASE_URL, candidate)
      .listSets()
      .then(() => {
        setAuth({ status: 'signed-in', token: candidate })
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
    setAuth({ status: 'signed-out', error: null })
  }, [])

  if (client === null) {
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
