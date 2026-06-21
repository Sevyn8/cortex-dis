# `libs/dis-storage/`

GCS path conventions, signed-URL generation, object metadata stamping, and path/metadata cross-checks. The single source of truth for how the platform places and accesses cross-tenant data in object storage.

```
libs/dis-storage/
├── pyproject.toml
├── README.md
├── src/
│   └── dis_storage/
│       ├── __init__.py
│       ├── paths.py            # build_object_path(...) + parse_object_path(...) (the only inverse)
│       ├── metadata.py         # build_object_metadata(...), assert_path_matches_metadata(...)
│       ├── signed_urls.py      # tenant-facing signed URLs for direct upload
│       ├── notifications.py    # parse GCS object-finalized notifications
│       └── client.py           # GCS client wrapper (honors STORAGE_EMULATOR_HOST)
└── tests/
    └── unit/
```

**Why this lib exists.** Multi-tenant data in a single GCS bucket is a leak risk if the path scheme drifts across producers. Four receivers and `tools/replay/` all write to GCS; without a shared library, each invents its own path code. One typo and tenant A's data lands under tenant B's prefix; IAM Conditions matching `tenant/{B}/**` then expose it. This lib makes drift impossible: every write goes through `build_object_path()`, every read through the same conventions, every object carries metadata that cross-checks the path.

**The path convention.** `gs://ithina-bronze-raw/tenant/{tenant_uuid}/source/{source_id}/yyyy={yyyy}/mm={mm}/dd={dd}/{trace_id}.{ext}`. The tenant segment is the **internal tenant UUID** (lowercase 8-4-4-4-12, `decisions.md` D53) — never an external code: the UUID is immutable while external codes are user-editable. Tenant prefix first (security boundary), source second (operational queries), date partitioning last (lifecycle rules). This is the battle-tested shape for object-storage multi-tenancy. `build_object_path()` builds it (coercing the tenant to canonical UUID text); `parse_object_path()` is the only sanctioned inverse.

**Why `signed_urls.py` exists.** Tenants upload large CSVs (manual upload, ERP POST). Routing those bytes through Ithina receivers wastes bandwidth and forces the receiver to handle large payloads in-memory. Instead, the receiver issues a signed URL scoped to exactly one object path, valid for ~15 minutes; the tenant PUTs directly to GCS. The receiver only handles a small object-finalized notification afterward. Standard pattern across major file-upload SaaS.

**Why `notifications.py` exists.** Object-finalized events arrive on Pub/Sub. The parsing (extract path, validate metadata, extract tenant_id from path, cross-check with metadata) is the same in every receiver. Single function in the lib.

**Why `client.py` wraps the GCS client.** The standard Google Cloud Storage client honors the `STORAGE_EMULATOR_HOST` environment variable; when set, all calls route to a local emulator (`fake-gcs-server`) instead of real GCS. Wrapping the client in the lib gives one place to enforce that, one place to add observability (counters, latency histograms), and one place to apply consistent timeout and retry policies. Same pattern that `clients/identity.py` uses for the Identity Service.

**What's deliberately not here.** No bucket lifecycle policy (lives in `infra/terraform/`). No IAM Conditions (lives in `infra/terraform/`). No bronze metadata write (that's a Postgres concern; lives in receivers' `sinks/bronze.py`). This lib is purely about object-storage conventions.

---
