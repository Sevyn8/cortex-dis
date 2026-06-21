# Slice 16a: create-template contract redesign (request shape only)

Reshapes the `POST /api/v1/mapping-templates` request so the frontend sends semantic
intent (source column to destination key) plus source-format declarations, instead of
pre-assembled engine ops. The endpoint accepts, shape-validates, returns 201 with a
synthetic response. It does NOT translate to `mapping_rules` and does NOT persist; that
arrives in 16c. Purpose: unblock the frontend (Amit) to integrate against a stable
contract immediately, while the translation layer and ops are built behind it.

## Depends on

- Slice 14b (the current create endpoint, handler, request/response schemas). 16a
  replaces the request schema and the handler body, keeps the route and the response
  shape.
- Slice 14d (`template_type`, the type-aware field catalog `build_field_catalogs`,
  `MODEL_BY_TYPE`, the per-type sink derivation). 16a's contract relies on the
  investigation-verified fact that the backend re-derives every catalog/sink fact from
  `template_type` + the canonical key, so the request need not echo sink objects.
- Investigation `docs/scratch/mapping-templates-create-investigation.md` (P1-P4).

## Decisions locked (for this slice)

- Endpoint path UNCHANGED: stays `POST /api/v1/mapping-templates`. The rename to a verb
  path was considered and dropped: `POST /mapping-templates` is already correct REST,
  and a verb path would split it from the GET/GET-by-id/PATCH siblings. (Register the
  drop-rename decision at the commit gate.)
- The request carries per-column `{src_key, dest_key, + format declarations}` and NOT
  the full sink object. The backend re-derives the sink semantics from `template_type` +
  `dest_key`; echoing them would be redundant and a drift surface. (`dest_key`'s value
  is the catalog field's `key` from `template-mapping-fields`. A later separate change
  may rename the catalog response `key` to `dest_key` for symmetry; out of 16a scope.)
- 16a validates SHAPE only; all semantic validation deferred to 16c.
- 16a does NOT write to `config.source_mappings` and does NOT build `mapping_rules`. It
  returns 201 with a synthetic `MappingTemplateDetail`-shaped body derived from the
  request (placeholder `template_id`, DRAFT status). Real persistence returns in 16c.
- Consequence accepted: from 16a until 16c lands, create persists nothing; a created
  template cannot be read back via GET/list. Acceptable (pre-production beta; short arc).

## Goal

After 16a, the frontend can POST the new request shape and get a deterministic 201 or a
shape-validation error, against the unchanged path, with the unchanged response shape.
Stable enough for Amit to integrate and rebuild the mapping UI flow now, before the
translation layer (16c) and new ops (16b) exist.

## The contract

### Request body

    {
      "source_id": "string, ^[a-z0-9_]{1,128}$",
      "template_name": "string, 1..200",
      "template_type": "snapshot | sales | inventory_change",
      "columns": [
        { "src_key": "sku",        "dest_key": "sku_id" },
        { "src_key": "prezzovend", "dest_key": "current_retail_price",
          "src_decimal_separator": ".", "src_thousand_separator": "," },
        { "src_key": "dataprezzo", "dest_key": "expiry_date",
          "src_datetime_format": "DD-MM-YYYY" },
        { "src_key": "in_volantino", "dest_key": "__ignore__" }
      ]
    }

Per-column fields:
- `src_key` (required): the source column header as it appears in the file.
- `dest_key` (required): the chosen canonical destination (the `key` value from the
  `template-mapping-fields` catalog for this `template_type`) or the reserved
  `"__ignore__"` to drop the column.
- Format declarations (present ONLY when needed; absent otherwise): `src_datetime_format`
  (string, e.g. `"DD-MM-YYYY"`), `src_decimal_separator` (`"."` or `","`),
  `src_thousand_separator` (`","` or `"'"`), `src_is_percentage` (bool `true`). These are
  declarations, not hints: mandatory exactly when the value cannot be parsed unambiguously
  without them. 16a checks they are WELL-FORMED if present; it does NOT check whether they
  are required for a given `dest_key` (16c).

### 16a validation (shape only)

Reject with the existing error envelope on:
- Body fails to parse / required top-level field missing (`source_id`, `template_name`,
  `template_type`, `columns`).
- `source_id` pattern, `template_name` length (as today).
- `template_type` not in the in-code vocabulary.
- A column missing `src_key` or `dest_key`.
- A present format-declaration field malformed (wrong type / disallowed value).

16a does NOT check: `dest_key` exists in the catalog, mandatory coverage, target
legality, presence pairings, duplicate semantics. (16c.)

### Response

- 201, body in the existing `MappingTemplateDetail` shape (unchanged from today).
- Since nothing is persisted, the body is synthetic: a placeholder `template_id`
  (server-minted UUIDv7 so the shape is realistic), `status` DRAFT, the echoed
  `source_id`/`template_name`/`template_type`, and a single synthetic DRAFT version entry.
  Mark clearly (handler/docstring) that this response is non-persisted in 16a.
- Errors: same envelope and status codes as today (422 shape/parse, 400 vocab, 401/403).

## Task

Plan-mode first. Build:
1. The new request Pydantic model(s) for the body and the column entries, replacing the
   old `MappingTemplateCreate` body model.
2. The handler change: parse + shape-validate + mint a placeholder `template_id` + build
   and return the synthetic `MappingTemplateDetail`. No repo call, no DB write, no
   `mapping_rules` assembly.
3. Keep `template_type` vocabulary validation (reuse the 14d check).
4. Update the affected tests (create POST tests move to the new request shape;
   GET/list/PATCH tests untouched).

Plan-mode grounding to confirm with file:line before building:
- The current `MappingTemplateCreate` body model + handler create path (what to replace)
  and the `MappingTemplateDetail` response builder (what to reuse for the synthetic body).
- The `template_type` vocab check to reuse.
- The exact error envelope + status mapping to keep parity.

## Scope

In: the new request model + column model; the handler accept/validate/synthetic-201
behaviour; `template_type` shape check; updated create tests.

Out (with where each lands):
- Translation of declarations + `dest_key` into `mapping_rules` and the DB write: 16c.
- Any new engine ops (e.g. `parse_percent`): 16b.
- Semantic validation (catalog membership, mandatory coverage, target legality, presence
  pairings): 16c.
- The endpoint rename: dropped.
- Catalog response `key` to `dest_key` rename: deferred, separate.
- Any edit to `services/dis-ui` (Amit's; READ-ONLY).

## Acceptance criteria

- `POST /api/v1/mapping-templates` accepts the new request shape and returns 201 with a
  `MappingTemplateDetail`-shaped synthetic body (placeholder `template_id`, DRAFT).
- Shape-validation rejections return the correct status/envelope: missing required field
  or bad column -> 422; bad `template_type` -> 400; auth/tenant -> 401/403.
- No row is written to `config.source_mappings` by this endpoint.
- `mapping_rules` is neither assembled nor stored in 16a.
- GET/list/PATCH endpoints and their tests are unchanged.
- `services/dis-ui` unmodified. `make check` / lint / mypy clean; tests in the same commit.

## Open questions for plan mode

1. Column-level strictness: reject unknown keys in a column object (strict) or ignore?
   Lean strict, so a malformed declaration surfaces early.
2. Synthetic response: mint a real UUIDv7 `template_id` (realistic) vs a clearly-fake
   sentinel id. Lean real UUIDv7; document it is non-persisted.
3. New body model beside the old one (kept for reference) or replace outright? Lean
   replace; the old shape is superseded.
