// Auth0 SPA configuration, resolved from the environment (FM: never hardcode
// ids/secrets in source). The domain/clientId/audience are the existing Cortex
// Auth0 application's PUBLIC values (a SPA client id is not a secret); they are
// supplied per environment via VITE_AUTH0_* and documented in .env.example.
//
// A missing value in Auth0 mode is a misconfiguration, not something to paper
// over with a default (root code-quality rule 4): we fail loud, mirroring
// mode.ts getBaseUrl. The redirect_uri is NOT config: it is
// window.location.origin at runtime (a registered callback on the application).

export type Auth0Config = {
  domain: string
  clientId: string
  audience: string
}

function required(name: string, value: string | undefined): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${name} is required when VITE_AUTH_MODE=auth0`)
  }
  return value
}

export function getAuth0Config(): Auth0Config {
  return {
    domain: required('VITE_AUTH0_DOMAIN', import.meta.env.VITE_AUTH0_DOMAIN),
    clientId: required('VITE_AUTH0_CLIENT_ID', import.meta.env.VITE_AUTH0_CLIENT_ID),
    audience: required('VITE_AUTH0_AUDIENCE', import.meta.env.VITE_AUTH0_AUDIENCE),
  }
}
