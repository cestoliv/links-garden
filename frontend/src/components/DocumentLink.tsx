interface DocumentLinkProps {
  title: string | null
  url: string | null
}

/** A document's title, linked out when it has a URL. Shared by the set tables and the review
 * queue so the title-or-url fallback logic lives in one place. */
export function DocumentLink({ title, url }: DocumentLinkProps) {
  if (url !== null) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer"
        className="font-medium text-emerald-700 hover:underline dark:text-emerald-400"
      >
        {title ?? url}
      </a>
    )
  }
  return <span className="font-medium text-zinc-900 dark:text-zinc-100">{title ?? 'Untitled'}</span>
}
