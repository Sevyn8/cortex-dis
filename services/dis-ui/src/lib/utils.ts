import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

// Class-name merge helper (clsx + tailwind-merge), matching the admin-frontend's
// design-system convention. Conditionally joins class lists and dedupes conflicting
// Tailwind utilities (the last wins). Used by the components/ui primitives.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
