# Ithina Master DB, Read Access for Platform Apps

## 1. Scope

Read-replica access to the Ithina master database from sibling platform applications.
Python / FastAPI reference snippets below; the contract is library-agnostic and works
through any driver, ORM, or connection pool.

## 2. The contract in 60 seconds

The master DB enforces tenant isolation via Postgres Row Level Security (RLS).
Tenant-scoped tables have `FORCE ROW LEVEL SECURITY` enabled and the application
DB role is `NOSUPERUSER NOBYPASSRLS`.

Every query, read or write, must run inside a transaction that has two session
variables (GUCs) set:

- `app.tenant_id`: the tenant UUID, as a string. `NULL` for PLATFORM users
  not impersonating a tenant.
- `app.user_type`: literal `'TENANT'` or `'PLATFORM'`.

If these are not set, queries against tenant-scoped tables return zero rows.
There is no error; RLS silently filters everything out. This is the most common
wiring mistake.

## 3. Visibility rules

| `app.user_type` | `app.tenant_id`         | What you see on tenant-scoped tables   |
|-----------------|-------------------------|----------------------------------------|
| `TENANT`        | tenant UUID             | Rows for that tenant only.             |
| `PLATFORM`      | tenant UUID (or empty)  | All rows across all tenants.           |
| (unset)         | (unset)                 | Nothing.                               |

Tenant-scoped tables in the `core` schema: `tenants`, `tenant_users`, `org_nodes`,
`stores`, `tenant_module_access`, `tenant_user_role_assignments`,
`tenant_activity_audit_logs`.

Tables without RLS (no GUCs required): `platform_users`, `roles`, `permissions`,
`role_permissions`, `platform_user_role_assignments`, `platform_activity_audit_logs`,
`lookups`, `alembic_version`.

## 4. AuthContext (the input)

Each request carries an authenticated context. Treat it as immutable:

```python
from pydantic import BaseModel
from typing import Literal
from uuid import UUID

class AuthContext(BaseModel, frozen=True):
    user_id: UUID
    user_type: Literal["PLATFORM", "TENANT"]
    tenant_id: UUID | None  # None for PLATFORM not impersonating
```

How `AuthContext` is populated is out of scope for this doc. The handoff from
admin-backend is the planned source; until that lands, use a dev shim that reads
`X-Tenant-Id` and `X-User-Type` request headers. **Never** trust tenant_id from
request body, query string, or unverified headers in production.

## 5. The session pattern (the only rule that matters)

Open a transaction, set both GUCs with `set_config(name, value, TRUE)`, run your
queries, commit or rollback. The third argument `is_local=TRUE` scopes the values
to the current transaction so they die on commit/rollback, which is safe under
connection pooling.

Use the `set_config()` function form, not the `SET LOCAL` statement form. Two
reasons:

- `set_config()` accepts `NULL` as the value cleanly. `SET LOCAL` has no clean
  way to represent NULL, which the PLATFORM-not-impersonating case needs
  (`app.tenant_id` NULL with `app.user_type` = 'PLATFORM' is the canonical shape).
- `set_config()` takes parameters through your driver's normal bind mechanism,
  so no string interpolation into SQL.

Raw SQL form (works through any driver):

```sql
BEGIN;
SELECT set_config('app.tenant_id', '019df261-b878-7c78-ad1c-da36f80aa17c', TRUE);
SELECT set_config('app.user_type', 'TENANT', TRUE);

-- your queries here

COMMIT;
```

For PLATFORM users not impersonating, pass `NULL` for `app.tenant_id`:

```sql
SELECT set_config('app.tenant_id', NULL, TRUE);
SELECT set_config('app.user_type', 'PLATFORM', TRUE);
```

The RLS policy wraps the read with `NULLIF(current_setting('app.tenant_id', TRUE), '')::uuid`,
so both `NULL` and empty string resolve correctly. `NULL` is the preferred form.



## 6. FastAPI dependency (reference shape)

Adapt this to your driver / ORM. The contract is: yield a session-like object
that has both GUCs set inside an open transaction.

```python
from collections.abc import AsyncIterator
from fastapi import Depends

async def get_replica_session(
    auth: AuthContext = Depends(get_auth_context),
) -> AsyncIterator[YourSession]:
    async with replica_engine.begin() as conn:
        await conn.execute(
            "SELECT set_config('app.tenant_id', :tenant_id, TRUE)",
            {"tenant_id": str(auth.tenant_id) if auth.tenant_id else None},
        )
        await conn.execute(
            "SELECT set_config('app.user_type', :user_type, TRUE)",
            {"user_type": auth.user_type},
        )
        yield conn
```

Key points:

- One transaction per request. Do not reuse the session across requests.
- `set_config(name, value, TRUE)`, the third argument is the `is_local` flag.
  `TRUE` scopes the value to the current transaction; `FALSE` would persist on
  the pooled connection and leak context between requests, do not use `FALSE`.
- Pass `tenant_id` as a string (or `None` for PLATFORM not impersonating).
- The same pattern works synchronously; just drop the `async` keywords.

## 7. Using it in a route

```python
@router.get("/stores")
async def list_stores(
    session = Depends(get_replica_session),
):
    rows = await session.execute("SELECT id, name FROM core.stores ORDER BY name")
    return rows.all()
```

RLS handles the tenant filter. Do not add `WHERE tenant_id = ...` in your query;
the policy already does that, and adding it manually is the path to bugs when
your code runs under PLATFORM context.

## 8. Do-not list

- Do not open connections outside the session dependency. One code path, one
  place to get GUCs right.
- Do not write to the read replica. It is read-only at the Postgres level;
  writes will error.
- Do not source `tenant_id` from request body, query string, or unverified
  headers in production. Only from the verified `AuthContext`.
- Do not cache query results across tenants in the same process without keying
  the cache by `tenant_id`.

## 9. Smoke test

Run this once after wiring is in place. Substitute a real tenant UUID.

```sql
BEGIN;
SELECT set_config('app.tenant_id', '<paste-tenant-uuid>', TRUE);
SELECT set_config('app.user_type', 'TENANT', TRUE);

SELECT COUNT(*) FROM core.tenant_users;   -- expect > 0
SELECT COUNT(*) FROM core.stores;         -- expect > 0

ROLLBACK;
```

Then flip to PLATFORM and confirm you see all tenants:

```sql
BEGIN;
SELECT set_config('app.tenant_id', NULL, TRUE);
SELECT set_config('app.user_type', 'PLATFORM', TRUE);

SELECT COUNT(*) FROM core.tenants;        -- expect total tenant count

ROLLBACK;
```

If either returns zero rows, the GUCs are not reaching the same transaction as
the query. That is the bug to find.

## 10. Connection details

Replica host, port, database name, and DB role come from the Ithina platform
GCP setup. Coordinate with the platform team for the exact connection string
and IAM binding. Use Cloud SQL Auth Proxy or Cloud SQL IAM authentication; do
not embed DB passwords in app config.
