// Snippets come straight out of the FTS index, so a vault note contributes its own markdown:
// `# Title`, `## Summary`, list bullets, `**bold**`. Rendered as-is they read as syntax noise in
// a result card, and the card cannot render markdown anyway — it clamps to four lines.
const BLOCK_MARKERS = /^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+|\d+\.[ \t]+)/gm
const EMPHASIS = /(\*\*|__|\*|_|`)/g

/** The snippet as one paragraph of plain text: markers dropped, blank lines collapsed. */
export function plainSnippet(text: string): string {
  return text
    .replace(BLOCK_MARKERS, '')
    .replace(EMPHASIS, '')
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '')
    .join(' ')
    .trim()
}
