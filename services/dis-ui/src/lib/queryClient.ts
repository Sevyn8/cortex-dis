import { QueryClient } from '@tanstack/react-query'

// The single app-wide TanStack Query client. Mounted via QueryClientProvider at
// the app root in App.tsx, above AuthProvider and AuthBoundary.
export const queryClient = new QueryClient()
