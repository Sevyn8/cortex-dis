# `libs/dis-testing/`

Shared test fixtures, fakes, and helpers. Used by all services' test suites.

```
libs/dis-testing/
├── pyproject.toml
├── README.md
├── src/
│   └── dis_testing/
│       ├── __init__.py
│       ├── fakes/
│       │   ├── identity_service.py     # fake Identity Service for service tests
│       │   ├── pubsub.py               # in-memory Pub/Sub
│       │   └── customer_master.py      # fake CM token issuer
│       ├── factories/                  # build test objects
│       │   ├── canonical_rows.py
│       │   ├── mappings.py
│       │   └── audit_events.py
│       └── docker_compose/             # the shared dev stack definition
│           └── docker-compose.yml
└── tests/
```

**Why this lib exists.** Without it, every service builds its own fake Identity Service, its own Pub/Sub mock, its own test object factories. Drift is inevitable; tests start passing in one service while integration breaks. Shared testing lib is how this is avoided.

---
