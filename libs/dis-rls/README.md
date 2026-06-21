# `libs/dis-rls/`

RLS-aware database session helpers. Wraps the `SET LOCAL app.tenant_id` pattern.

```
libs/dis-rls/
├── pyproject.toml
├── README.md
├── src/
│   └── dis_rls/
│       ├── __init__.py
│       ├── session.py          # context manager: open tx, set tenant, run, commit
│       ├── batch.py            # batched-by-tenant transaction wrapper
│       └── enforcement.py      # assertions: this connection has tenant set
└── tests/
    └── unit/
```

**Why this lib exists.** Every service that writes to canonical, history, or quarantine *must* set the tenant GUC inside the transaction. Centralizing the pattern makes it impossible to forget. `enforcement.py` provides runtime assertions that fail loudly if a write attempts to happen outside an RLS-scoped session.

---
