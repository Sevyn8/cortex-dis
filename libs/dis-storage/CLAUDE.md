# libs/dis-storage — Claude Code Context

Loaded when Claude Code works in `libs/dis-storage/`. Lib-specific rules; root `CLAUDE.md` applies first.

## What this lib is

GCS access: the frozen canonical object-path scheme, V4 signed-URL issuance, and an
emulator-honouring client wrapper. The only sanctioned GCS access path in DIS
(hard rule 9).

For interfaces, types, file structure, see `README.md`.

## Rules specific to this lib (Slice 4)

- **All GCS access goes through this lib.** Direct `google-cloud-storage` import
  elsewhere is forbidden by CI lint.
- **Frozen path scheme** (do NOT improvise):
  `tenant/{tenant_uuid}/source/{id}/yyyy=Y/mm=M/dd=D/{trace_id}.{ext}`. The tenant
  segment is the **internal tenant UUID** (lowercase 8-4-4-4-12, D53), never an
  external code. `build_object_path` builds it (non-UUID tenant → `StorageError`;
  date segments normalised to UTC); `parse_object_path` is the only sanctioned
  inverse (tenant typed `UUID`, `trace_id` returned verbatim as `str`).
- **Never mint `trace_id`** (hard rule 4): the caller supplies it; the path builder
  echoes it verbatim and imports no trace-id generator.
- **`client.py` honours `STORAGE_EMULATOR_HOST`** (anonymous creds + endpoint
  override → fake-gcs-server). One place for object read/write.
- **Signed URLs are scoped to exactly one object path** — never a wildcard. V4
  signing is deterministic and offline. **A well-formed URL is NOT proof real GCS
  accepts the signature**; that is unverified until a real-GCS slice (first use:
  Slice 8's 15-minute PUT URL). The PUT/GET round-trip *through* a signed URL is
  deferred (fake-gcs-server does not honour signatures); wrapper object access and
  issuance shape are tested.
- **Tests sign with a throwaway test credential only** — never a real service account.
- Errors are `dis-core` `StorageError` (rooted in `DisError`); never raw
  `RuntimeError`/`ValueError`.
- Build to current need: `metadata.py` / `notifications.py` from the README tree are
  deferred to the receiver slices that consume them.

## References

- `README.md` — interface and structure.
- Root `CLAUDE.md` — project-wide invariants.
