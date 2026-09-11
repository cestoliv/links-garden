export interface ButtonStyleProps {
  variant?: 'primary' | 'ghost'
  size?: 'sm' | 'md'
}

const base =
  'inline-flex cursor-pointer items-center justify-center gap-2 rounded-md font-medium ' +
  'transition-[background-color,border-color,color] duration-150 ease-out ' +
  'disabled:cursor-not-allowed disabled:opacity-50 ' +
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-400'

const sizes = {
  md: 'px-3.5 py-2 text-sm',
  sm: 'px-2.5 py-1 text-xs',
}

// Primary carries dark text on the accent: white on this green misses 4.5:1. Ghost is a
// bordered slot, so a row of mixed actions still lines up.
const variants = {
  primary: 'bg-emerald-500 text-zinc-950 hover:bg-emerald-400',
  ghost:
    'border border-zinc-300 bg-transparent text-zinc-700 hover:bg-zinc-200/70 ' +
    'dark:border-zinc-700 dark:text-zinc-200 dark:hover:border-zinc-600 dark:hover:bg-zinc-800',
}

/** `Button`'s own look, kept out of Button.tsx so that file only exports the component (fast
 * refresh requires that). Also used directly for a control that has to be a real `<a>` instead
 * (an in-app link styled as a secondary action, e.g. "Graph" on a document row). */
export function buttonClassName({ variant = 'primary', size = 'md' }: ButtonStyleProps = {}): string {
  return `${base} ${sizes[size]} ${variants[variant]}`
}
