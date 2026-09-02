import type { ButtonHTMLAttributes } from 'react'
import { buttonClassName } from './buttonClassName'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'ghost'
  size?: 'sm' | 'md'
}

// A shared button so the press feedback (scale on :active, fast ease-out) lives in one place
// instead of being re-tuned per call site. `size: 'sm'` is for dense row actions (table cells,
// review-queue forms) where the default padding is too big.
export function Button({ variant = 'primary', size = 'md', className = '', ...props }: ButtonProps) {
  return <button className={`${buttonClassName({ variant, size })} ${className}`} {...props} />
}
