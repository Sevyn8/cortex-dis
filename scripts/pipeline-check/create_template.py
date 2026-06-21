"""Script A — create a source-mapping template (replicates the dis-ui POST).

Reads an operator-supplied dis-ui semantic mapping spec
(``{source_id, template_name, template_type, columns[...]}`` with
``src_key``/``dest_key``/``__ignore__``/``src_decimal_separator`` etc.) and POSTs
it to ``POST /api/v1/mapping-templates`` with a minted LOCAL TENANT token. The
backend translates the columns into mapping_rules, validates, and writes a single
ACTIVE row to config.source_mappings. No UI, no LLM.

Usage:
  uv run python scripts/pipeline-check/create_template.py \
      --spec scripts/pipeline-check/local/inputs/snapshot-spec.json \
      [--tenant <uuid>] [--base http://localhost:8080] \
      [--out scripts/pipeline-check/local/out/template.json] [--no-stack]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx
from _common import (  # noqa: E402  (script-local import; run via uv run python)
    DEFAULT_BASE_URL,
    OUT_DIR,
    auth_header,
    ensure_stack,
    mint_tenant_token,
    seeded_tenant_uuid,
)

_REQUIRED_SPEC_KEYS = ("source_id", "template_name", "template_type", "columns")


def _load_spec(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"spec not found: {path}")
    spec = json.loads(path.read_text())
    missing = [k for k in _REQUIRED_SPEC_KEYS if k not in spec]
    if missing:
        raise SystemExit(f"spec missing required top-level key(s): {missing}")
    if not isinstance(spec["columns"], list) or not spec["columns"]:
        raise SystemExit("spec 'columns' must be a non-empty list")
    return spec


def _print_error(resp: httpx.Response) -> None:
    """Surface the §2.3 error envelope (422/400/403/409) clearly."""
    print(f"[A] HTTP {resp.status_code}", file=sys.stderr)
    try:
        body = resp.json()
    except Exception:
        print(resp.text, file=sys.stderr)
        return
    err = body.get("error", body)
    print(f"    code:    {err.get('code')}", file=sys.stderr)
    print(f"    message: {err.get('message')}", file=sys.stderr)
    if err.get("details"):
        print(f"    details: {json.dumps(err['details'])}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Create a DIS source-mapping template (Script A).")
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--tenant", default=None, help="tenant UUID (default: seeded fixture tenant)")
    ap.add_argument("--base", default=DEFAULT_BASE_URL)
    ap.add_argument("--out", default=OUT_DIR / "template.json", type=Path)
    ap.add_argument("--no-stack", action="store_true")
    args = ap.parse_args()

    ensure_stack(skip=args.no_stack, base=args.base)

    tenant = args.tenant or seeded_tenant_uuid()
    spec = _load_spec(args.spec)
    token = mint_tenant_token(tenant)

    url = f"{args.base}/api/v1/mapping-templates"
    print(f"[A] POST {url}  (tenant={tenant}, type={spec['template_type']}, columns={len(spec['columns'])})")
    resp = httpx.post(url, json=spec, headers=auth_header(token), timeout=30.0)

    if resp.status_code != 201:
        _print_error(resp)
        return 1

    detail = resp.json()
    template_id = detail["template_id"]
    source_id = detail.get("source_id", spec["source_id"])
    template_type = detail.get("template_type", spec["template_type"])
    print(f"[A] 201 Created  template_id={template_id}  source_id={source_id}  type={template_type}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "template_id": template_id,
                "source_id": source_id,
                "template_type": template_type,
                "tenant_uuid": tenant,
            },
            indent=2,
        )
    )
    print(f"[A] wrote {args.out} for Script B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
