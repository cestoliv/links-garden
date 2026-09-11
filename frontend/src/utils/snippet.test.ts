import { describe, expect, it } from 'vitest'
import { plainSnippet } from './snippet'

describe('plainSnippet', () => {
  it('drops block markers and joins the remaining lines', () => {
    expect(plainSnippet('# Title\n\n## Summary\n- one\n- two')).toBe('Title Summary one two')
  })

  it('drops inline emphasis without touching the words', () => {
    expect(plainSnippet('a **bold** and `code` word')).toBe('a bold and code word')
  })

  it('leaves plain text unchanged', () => {
    expect(plainSnippet('Une liste d’idées de diaporamas…')).toBe('Une liste d’idées de diaporamas…')
  })
})
