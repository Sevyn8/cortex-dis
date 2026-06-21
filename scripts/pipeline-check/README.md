# pipeline-check — operator pipeline-integrity scripts

Two command-line scripts to verify DIS pipeline integrity end to end against a
**local** stack, with minimum intervention:

- **`create_template.py` (Script A)** — create a source-mapping template exactly
  as dis-ui would (no UI, no LLM), from an operator-supplied mapping spec.
- **`verify_ingest.py` (Script B)** — push a data CSV through the full pipeline and
  report where it landed (canonical vs quarantine), correlated by `trace_id`.

These are **operator tooling only**. They call the real HTTP endpoints, mint a
local dev JWT, optionally bring the stack up, and read the DB **read-only**. They
do not modify any service/lib/schema/pipeline code.

## Layout

```
scripts/pipeline-check/
  README.md            # this file (tracked)
  _common.py           # shared: token mint, stack readiness, RLS read helper (tracked)
  create_template.py   # Script A (tracked)
  verify_ingest.py     # Script B (tracked)
  local/               # GITIGNORED — your data + run outputs, never tracked
    inputs/            #   mapping-spec JSONs + data CSVs you provide
    out/               #   template.json, *.varied.csv, run-<trace_id>.json
```

## Prerequisites

The scripts call `scripts/run_dis_on_local start` for you (idempotent — reuses
running services, runs `make run-local` + `make check` + seed + mirror-sync, and
starts dis-ui-server + both workers). Pass `--no-stack` if you manage the stack
yourself. `POSTGRES_URL` must be set (it is, via `.env`).

## Usage

```bash
# A. create a template from a spec in local/inputs/
uv run python scripts/pipeline-check/create_template.py \
    --spec scripts/pipeline-check/local/inputs/snapshot-spec.json
# -> prints template_id, writes local/out/template.json

# B. upload a data CSV and verify landing (uses local/out/template.json)
uv run python scripts/pipeline-check/verify_ingest.py \
    --csv  scripts/pipeline-check/local/inputs/snapshot.csv \
    --spec scripts/pipeline-check/local/inputs/snapshot-spec.json
# -> leads with a VERDICT banner (LANDED/QUARANTINED + trace_id + counts), then the
#    pipeline progression grouped by service, then sampled canonical sku_ids;
#    writes the FULL detail to local/out/run-<trace_id>.json

# unattended (skip the vary confirmation prompt):
uv run python scripts/pipeline-check/verify_ingest.py ... --yes
# --verbose: show the full run_dis_on_local block + the full sku_id list inline
```

### Reading Script B's output

The output is **verdict-first**: a banner with `VERDICT` (LANDED / QUARANTINED / TIMEOUT),
the `trace_id`, and the canonical/quarantine row counts; then the audit **progression grouped
by service** (`dis-ui-server` → `csv-ingest-worker` → `streaming-consumer`) with `✓`/`✗` per
stage and `← stopped here` on the failing phase; then a **sample** of canonical sku_ids
(count + first few; `--verbose` for all). The stack-startup block is collapsed to one
`✓ stack ready` line (full block on failure or `--verbose`). The terminal samples — the
**full** sku_id list, quarantine detail, and service-attributed audit go to
`local/out/run-<trace_id>.json`, so nothing is lost.

Inputs live in `local/inputs/`; outputs go to `local/out/`. Both are gitignored.

The mapping spec is the **dis-ui semantic shape** (NOT `mapping_rules`):

```json
{
  "source_id": "ops_snapshot_csv",
  "template_name": "ops snapshot",
  "template_type": "snapshot",
  "columns": [
    { "src_key": "sku_id", "dest_key": "sku_id" },
    { "src_key": "current_retail_price", "dest_key": "current_retail_price", "src_decimal_separator": "." },
    { "src_key": "notes", "dest_key": "__ignore__" }
  ]
}
```

## What these scripts depend on in the system (and where it lives)

Each assumption is tied to its code so a future edit is informed:

| Assumption the scripts encode | Where it lives in code |
|---|---|
| Create body is the semantic `columns[]` shape; backend translates → mapping_rules | `services/dis-ui-server/.../mapping_translation.py`; handler `handlers/mapping_templates.py:161` |
| Create writes a single **ACTIVE** row to `config.source_mappings` | `repos/mapping_templates.py` (`create_template`, status ACTIVE) |
| Upload is `POST /api/v1/csv-uploads`, multipart `file` + `template_id` + `store_code`, **TENANT-only** | `handlers/csv_uploads.py:157-199` (`require_tenant`) |
| `trace_id` is minted at upload and is the single correlation key across all destinations | `handlers/csv_uploads.py:164`; `streaming-consumer/.../engine/normalize.py` |
| Snapshot lands in `canonical.store_sku_current_position` (no D63 catalogue-before-sales dependency) | `streaming-consumer/.../sinks/canonical.py` |
| Store must exist & be **ACTIVE** in `identity_mirror.stores` before upload | resolved at `handlers/csv_uploads.py:215`; seeded by `make seed` / `libs/dis-testing` |
| Local TENANT token, minted to match the verifier dev-stub; tenant read from `fixtures.PRIMARY_TENANT` (same source as `make seed`) | `auth/verifier.py:32-35`; `gen_dis_jwt_90d_v1.sh:83-110`; `libs/dis-testing/.../fixtures.py:239` |
| The `scripts/jwt/tokens/` v1 files are **GCP-deployment** tokens (GCP tenant UUIDs) — distinct by design from local seeded tenants; the scripts mint the **local** counterpart | `scripts/jwt/gen_dis_jwt_90d_v1.sh:43-62` vs `fixtures.py:160-227` |
| Stack readiness via the existing orchestrator (not reimplemented) | `scripts/run_dis_on_local` (`start\|stop\|status`) |
| Read-only observation needs **both** RLS GUCs (`app.user_type`,`app.tenant_id`) set | `libs/dis-rls/.../session.py`; `libs/dis-testing/.../seed.py` |
| Audit poll keys on `stage='CANONICAL_WRITTEN'`/`outcome='SUCCESS'` (success) and `outcome='FAILURE'`/`stage='QUARANTINED'` (failure) | `libs/dis-audit/src/dis_audit/stages.py` |
| Landing/quarantine/audit column names queried | grounding `docs/scratch/pipeline-integrity-scripts-grounding.md` §B9 |

## Known traps

- **Byte-dedup (24h window).** Re-uploading identical bytes for the same
  tenant/store/template within 24h is a no-op. Script B auto-varies **one cell of an
  `__ignore__` column** (provably non-canonical — dropped at translation, never
  lands) and prompts before uploading (`--yes` to skip). **Your spec must include at
  least one `__ignore__` column.**
- **`src_key` is an exact, case- and whitespace-sensitive match** against the data
  CSV header. A mismatch silently drops the column → a mandatory field goes missing →
  quarantine. Keep the spec's `src_key`s aligned to the data CSV header.
- **Async, no completion signal.** Landing is two Pub/Sub hops after the 201; Script B
  polls audit by `trace_id` (bounded by `--timeout`, default 30s). A `TIMEOUT` verdict
  usually means a worker isn't running.
- **Single-instance csv-ingest-worker.** Don't run two; the dedup is query-based.
- **Semicolon delimiter** is auto-detected downstream (the worker's DuckDB sniff);
  Script B also sniffs (`;` or `,`) locally only to vary the cell.

## What to check when the system changes

If you change one of these, update the script accordingly (and only the script):

- **Create request model** (`schemas/mapping_templates.py`) → update Script A's spec/body.
- **Upload multipart fields / auth** (`handlers/csv_uploads.py`) → update Script B's upload call.
- **Audit stage/outcome literals** (`libs/dis-audit/.../stages.py`) → update the poll
  constants at the top of `verify_ingest.py`. (If the happy path returns `TIMEOUT`
  instead of `LANDED`, the literal is stale — fix it here, nowhere else.)
- **Audit service attribution** — the progression groups by `audit.events.service_name`
  (read live, no hardcoded map). If a new service emits audit for an upload it just appears
  as a new group; the service-name strings track each service's `config.SERVICE_NAME`.
- **RLS GUC names** (`libs/dis-rls/.../session.py`) → update `rls_session` in `_common.py`.
- **Auth moves to real Auth0/JWKS (D25)** → the dev-stub HS256 `mint_tenant_token`
  helper in `_common.py` is the **single** auth-coupled point that must be replaced.
- **Seeded fixture tenant/store** (`libs/dis-testing/.../fixtures.py`) → no code edit
  needed (the scripts read `PRIMARY_TENANT`/`PRIMARY_STORE` at runtime); just re-run `make seed`.
- **Destination columns** (grounding §B9) → update the landing queries in `verify_ingest.py`.

### Worked example — making a `store_sku_current_position` column NULLABLE

Suppose a currently-required column (say `unit_cost`) is made NULLABLE in the
canonical schema. Script impact:

- The **landing query** still works unchanged (it's read-only and selects key/lineage
  columns, not that field).
- More importantly, a field going nullable may **drop out of create-time
  mandatory-coverage** (`mapping_validation.mandatory_mapping_produced` derives the
  mandatory set from the model's *required* fields). So a `dest_key` that was
  previously required-to-map could become optional — an existing snapshot spec that
  omitted it would now be accepted where it previously 400'd. Revisit your specs if
  you rely on that field landing.
- **No frontend (services/dis-ui) change is expected** for the scripts; the catalog is
  model-derived and the scripts send the same semantic shape.

(Awareness note only — the actual nullable decision is a separate thread, not part of
this tooling.)
