import type { AnchorHTMLAttributes, MouseEvent } from 'react'

interface LinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  href: string
  onNavigate: () => void
}

/** A real `<a>` for every in-app route, so middle-click and "open in new tab" work like any other
 * link. Only a plain left-click is intercepted, to hand the navigation to the router instead of a
 * full page load; a modified click (ctrl/cmd/shift/alt) or a middle-click falls through untouched. */
export function Link({ href, onNavigate, onClick, ...props }: LinkProps) {
  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event)
    if (event.defaultPrevented || event.button !== 0) return
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    onNavigate()
  }

  return <a href={href} onClick={handleClick} {...props} />
}
