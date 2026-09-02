interface ComingSoonProps {
  title: string
  description: string
}

// Placeholder for the four pages later tasks build: a real route rather than a disabled link,
// so the nav never lies about what exists.
export function ComingSoon({ title, description }: ComingSoonProps) {
  return (
    <div className="flex flex-col items-start gap-1 px-6 py-16 text-zinc-500 dark:text-zinc-400">
      <h2 className="text-base font-medium text-zinc-700 dark:text-zinc-300">{title}</h2>
      <p className="max-w-md text-sm">{description}</p>
    </div>
  )
}
