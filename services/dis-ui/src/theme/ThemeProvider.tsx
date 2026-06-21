import type { ReactNode } from 'react'
import { ThemeProvider as NextThemesProvider } from 'next-themes'

// Light/dark theming for the DIS UI design system (slice 23). Class-based: the
// `.dark` class on <html> flips the oklch token set defined in index.css. next-themes
// is framework-agnostic React, so it works under Vite (no next/font, no SSR script
// needed in this CSR app). Persists the choice and respects the system preference.
export function ThemeProvider({ children }: { children: ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="light"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  )
}
