import type { ReactNode } from 'react'

interface PageHeaderProps {
  /** The small label above the title, naming the section this page belongs to. */
  eyebrow: string
  title: string
  description?: string
  /** Page-level controls (a filter, a "New set" button), right-aligned on wide screens. */
  actions?: ReactNode
}

// Every page opens the same way, so a reader lands in the same place each time: label, title,
// one line of what this page is for, then a hairline that separates chrome from content.
export function PageHeader({ eyebrow, title, description, actions }: PageHeaderProps) {
  return (
    <div className="border-b border-zinc-200 pb-5 dark:border-zinc-800">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="hud-label">{eyebrow}</p>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">{title}</h1>
          {description !== undefined && (
            <p className="mt-1.5 max-w-2xl text-sm text-zinc-500 dark:text-zinc-400">{description}</p>
          )}
        </div>
        {actions !== undefined && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    </div>
  )
}
