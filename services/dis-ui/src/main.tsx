import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Fonts under Vite (the DIS UI cannot use next/font). T7: Inter is the app-wide sans
// face (matching the Ithina console); Geist Mono stays for code/monospace. Fontsource
// registers the 'Inter' / 'Geist Mono' families the --font-* tokens bind to in index.css.
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/geist-mono/400.css'

import './index.css'
import App from './App.tsx'
import { ThemeProvider } from './theme/ThemeProvider.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
)
