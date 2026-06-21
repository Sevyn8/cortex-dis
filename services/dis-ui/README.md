# services/dis-ui (v1.0)

The DIS UI: the single containerized frontend SPA for DIS. It hosts every user-facing screen for tenants and Ithina ops, delegates auth to Customer Master, and calls one backend, `dis-ui-server`.

**Purpose.** Give DIS one human-facing front door (one URL, one auth integration). The UI renders the platform's surfaces (sample upload, onboarding review, mapping CRUD, quarantine console, audit lookup, ops DuckDB panel) and never talks to data-plane services directly. See `docs/architecture.md` section 4.13.

**Entry.**
- Trigger: a browser loads the SPA (static `index.html` + bundle) and navigates a route.
- Inputs: the route path; a Customer Master token. In slice 19 the token is an HMAC-signed stub minted at `/dev/login` and held in localStorage.
- Preconditions: every route except `/dev/login` requires a valid token.

**Process.**
- `AuthBoundary` verifies the token (`src/auth/verifyToken.ts`) and populates an in-memory `AuthSnapshot` via React context; unauthenticated users are redirected to `/dev/login`.
- react-router resolves the path to a page under the authenticated layout shell.
- Pages read and write through `src/lib/dis-ui-server/*` (TanStack Query). In fixture mode (the slice-19 default) the client returns local fixtures and makes no network call; in real mode it calls dis-ui-server with the bearer token (wired, not implemented this slice).

**Exit.**
- Output: the rendered page; the app is served as static SPA assets from the container.
- All backend traffic goes to dis-ui-server only. There are no canonical or data-plane writes from the UI.

**Stack (actual installed versions).**
- Node 22, pnpm 10.
- Vite 8, React 19, TypeScript 6 (strict).
- Tailwind CSS v4, CSS-first via `@tailwindcss/vite` (no PostCSS, no `tailwind.config.js`).
- react-router 7; TanStack Query 5.
- jose 6 (stub JWT sign/verify; the JWKS swap seam for real Customer Master).
- Vitest 4 + React Testing Library.

**Quick start (from `services/dis-ui/`).**
- `pnpm install`
- `pnpm dev` - dev server on http://localhost:5173
- `pnpm test` - Vitest
- `pnpm build` - production bundle
- `pnpm lint` / `pnpm tsc` - ESLint / strict type-check

**Directory structure.**
```
services/dis-ui/
├── CLAUDE.md
├── README.md
├── .env.example              # VITE_DIS_UI_SERVER_MODE / _BASE_URL
├── index.html
├── package.json
├── vite.config.ts            # react + tailwind v4 + vitest
├── eslint.config.js
├── tsconfig*.json
├── docs/
│   └── dis-ui-server-contract.md   # the dis-ui-server contract the UI consumes
└── src/
    ├── App.tsx               # QueryClientProvider + AuthProvider + BrowserRouter
    ├── main.tsx
    ├── index.css             # @import "tailwindcss"
    ├── auth/                 # AuthSnapshot, AuthProvider/context/useAuth, AuthBoundary, verifyToken, storage
    │   └── dev/              # stub-JWT secret, personas, signStubToken (dev only)
    ├── lib/
    │   ├── queryClient.ts
    │   └── dis-ui-server/    # types, mode, client, fixtures, me (getMe + useMe)
    ├── routes/               # AppRoutes, AppLayout, DevLogin, Home
    └── test/                 # setup.ts (jsdom realm fix), renderWithProviders
```

**Status.** v1.0 foundation (slice 19): scaffolding, AuthBoundary stub auth, and a hello-world Home that reads the signed-in user from a stubbed dis-ui-server client. The 11 product screens land in slice 20 and beyond.
