export interface ButtonStyleProps {
  variant?: 'primary' | 'ghost'
  size?: 'sm' | 'md'
}

const base =
  'inline-flex items-center justify-center gap-2 rounded-md font-medium ' +
  'transition-[transform,background-color] duration-150 ease-out active:scale-[0.97] ' +
  'disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 ' +
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600'

const sizes = {
  md: 'px-4 py-2 text-sm',
  sm: 'px-2.5 py-1 text-xs',
}

const variants = {
  primary: 'bg-emerald-700 text-white hover:bg-emerald-600 dark:bg-emerald-600 dark:hover:bg-emerald-500',
  ghost:
    'bg-transparent text-zinc-700 hover:bg-zinc-200/70 dark:text-zinc-300 dark:hover:bg-zinc-800',
}

/** `Button`'s own look, kept out of Button.tsx so that file only exports the component (fast
 * refresh requires that). Also used directly for a control that has to be a real `<a>` instead
 * (an in-app link styled as a secondary action, e.g. "Graph" on a document row). */
export function buttonClassName({ variant = 'primary', size = 'md' }: ButtonStyleProps = {}): string {
  return `${base} ${sizes[size]} ${variants[variant]}`
}
