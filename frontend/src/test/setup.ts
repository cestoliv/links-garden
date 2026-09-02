import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import '@testing-library/jest-dom/vitest'

// Without `test.globals` in vite.config.ts, Testing Library can't auto-detect `afterEach`
// to run its own cleanup, so each test would leak DOM into the next.
afterEach(() => {
  cleanup()
})
