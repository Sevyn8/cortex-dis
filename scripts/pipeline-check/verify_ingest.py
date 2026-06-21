"""Script B — push a data CSV through the full pipeline and report where it landed.

End to end: precondition checks -> auto-vary one __ignore__ cell (defeat byte
dedup) -> POST /api/v1/csv-uploads (multipart) -> poll audit.events by trace_id
-> report landing in canonical.store_sku_current_position OR quarantine.*, with
the audit stage progression. Everything is correlated by the single key trace_id.

Usage:
  uv run python scripts/pipeline-check/verify_ingest.py \
      --csv scripts/pipeline-check/local/inputs/snapshot.csv \
      --spec scripts/pipeline-check/local/inputs/snapshot-spec.json \
      [--template scripts/pipeline-check/local/out/template.json | --template-id <uuid>] \
      [--store-code TX-101] [--tenant <uuid>] [--timeout 30] [--yes] [--no-stack] [--base ...]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import sys
import time
from pathlib import Path

import httpx
from _common import (  # noqa: E402  (script-local import; run via uv run python)
    DEFAULT_BASE_URL,
    OUT_DIR,
    auth_header,
    ensure_stack,
    mint_tenant_token,
    rls_query,
    seeded_store_code,
    seeded_tenant_uuid,
)

# Audit literals the poll keys on — from libs/dis-audit/src/dis_audit/stages.py.
STAGE_SUCCESS_TERMINAL = "CANONICAL_WRITTEN"
STAGE_QUARANTINED = "QUARANTINED"
OUTCOME_SUCCESS = "SUCCESS"
OUTCOME_FAILURE = "FAILURE"


# --- inputs -------------------------------------------------------------------
def _resolve_template(args) -> tuple[str, str | None, str | None]:
    """Return (template_id, template_type, tenant_uuid) from --template-id or the file."""
    if args.template_id:
        return args.template_id, None, None
    path = Path(args.template)
    if not path.exists():
        raise SystemExit(
            f"template ref not found: {path} (run create_template.py first, or pass --template-id)"
        )
    data = json.loads(path.read_text())
    return data["template_id"], data.get("template_type"), data.get("tenant_uuid")


def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:5])
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,").delimiter
    except csv.Error:
        return ";" if sample.count(";") >= sample.count(",") else ","


def _ignore_src_key(spec: dict) -> str:
    for col in spec["columns"]:
        if col.get("dest_key") == "__ignore__":
            return col["src_key"]
    raise SystemExit(
        "spec has no column mapped to '__ignore__'; add one so the run can vary a "
        "non-canonical cell to defeat 24h byte-dedup"
    )


def _vary_cell(csv_path: Path, ignore_key: str, *, assume_yes: bool) -> tuple[bytes, str]:
    """Mutate ONE cell in the __ignore__ column (never lands); show + confirm."""
    text = csv_path.read_text()
    delim = _sniff_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if len(rows) < 2:
        raise SystemExit(f"{csv_path}: need a header + at least one data row")
    header = rows[0]
    if ignore_key not in header:
        raise SystemExit(f"__ignore__ column {ignore_key!r} not found in CSV header {header}")
    idx = header.index(ignore_key)
    old = rows[1][idx] if idx < len(rows[1]) else ""
    marker = dt.datetime.now(tz=dt.UTC).strftime("%Y%m%d%H%M%S%f")
    new = f"{old}-v{marker}"
    while len(rows[1]) <= idx:
        rows[1].append("")
    rows[1][idx] = new

    suffix = "   (--yes)" if assume_yes else ""
    print(f"[B] dedup-vary: {ignore_key} (__ignore__)  {old!r} → {new!r}{suffix}")
    if not assume_yes:
        if input("    proceed with this varied upload? [y/N] ").strip().lower() not in ("y", "yes"):
            raise SystemExit("aborted by operator")

    buf = io.StringIO()
    csv.writer(buf, delimiter=delim, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8"), delim


# --- audit poll ---------------------------------------------------------------
# Audit rows are 4-tuples: (service_name, stage, outcome, event_timestamp).
# service_name is the canonical hop attribution (RECEIVED is emitted by all three
# services); it is display-only here — the terminal predicates below key on
# stage/outcome exactly as before.
def _poll_audit(tenant: str, trace_id: str, timeout_s: int) -> list[tuple]:
    deadline = time.monotonic() + timeout_s
    last: list[tuple] = []
    while True:
        rows = rls_query(
            tenant,
            "SELECT service_name, stage, outcome, event_timestamp FROM audit.events "
            "WHERE trace_id = %s::uuid ORDER BY event_timestamp",
            (trace_id,),
        )
        last = rows
        terminal_ok = any(s == STAGE_SUCCESS_TERMINAL and o == OUTCOME_SUCCESS for _svc, s, o, _ts in rows)
        terminal_bad = any(o == OUTCOME_FAILURE or s == STAGE_QUARANTINED for _svc, s, o, _ts in rows)
        if terminal_ok or terminal_bad or time.monotonic() >= deadline:
            return last
        time.sleep(1.5)


# --- landing report -----------------------------------------------------------
_BAR = "=" * 68
_SAMPLE_N = 5


def _group_by_service(audit_rows: list[tuple]) -> list[tuple[str, list[tuple[str, str]]]]:
    """Group (service_name, stage, outcome, ts) rows by service in first-seen order.

    Returns [(service_name, [(stage, outcome), ...]), ...] — the natural pipeline
    order, since rows arrive ORDER BY event_timestamp.
    """
    order: list[str] = []
    by_svc: dict[str, list[tuple[str, str]]] = {}
    for svc, stage, outcome, _ts in audit_rows:
        if svc not in by_svc:
            by_svc[svc] = []
            order.append(svc)
        by_svc[svc].append((stage, outcome))
    return [(svc, by_svc[svc]) for svc in order]


def _render_progression(audit_rows: list[tuple]) -> None:
    print("  Pipeline progression (by service):")
    for svc, stages in _group_by_service(audit_rows):
        parts: list[str] = []
        for stage, outcome in stages:
            mark = "✓" if outcome == OUTCOME_SUCCESS else "✗"
            tag = f"{stage} {mark}"
            if outcome == OUTCOME_FAILURE:
                tag += "  ← stopped here"
            parts.append(tag)
        print(f"    {svc:<19} " + " · ".join(parts))


def _report(tenant: str, trace_id: str, audit_rows: list[tuple], *, verbose: bool) -> str:
    cp = rls_query(
        tenant,
        "SELECT sku_id, mapping_version_id, dis_channel, last_updated_at "
        "FROM canonical.store_sku_current_position WHERE trace_id = %s::uuid ORDER BY sku_id",
        (trace_id,),
    )
    qchunks = rls_query(
        tenant,
        "SELECT failure_stage, failure_reason, row_count_in_chunk "
        "FROM quarantine.quarantined_chunks WHERE trace_id = %s::uuid",
        (trace_id,),
    )
    qrows = rls_query(
        tenant,
        "SELECT row_offset, failure_stage, failure_reason "
        "FROM quarantine.quarantined_rows WHERE trace_id = %s::uuid",
        (trace_id,),
    )
    reached_ok = any(s == STAGE_SUCCESS_TERMINAL and o == OUTCOME_SUCCESS for _svc, s, o, _ts in audit_rows)
    has_quarantine = bool(qchunks or qrows) or any(
        o == OUTCOME_FAILURE or s == STAGE_QUARANTINED for _svc, s, o, _ts in audit_rows
    )

    if cp and reached_ok:
        verdict = "LANDED"
    elif has_quarantine:
        verdict = "QUARANTINED"
    else:
        verdict = "TIMEOUT"

    # --- verdict-first banner ---
    print(f"\n{_BAR}")
    print(f"  VERDICT: {verdict}        trace_id {trace_id}")
    print(f"  canonical rows: {len(cp):<6} quarantine rows: {len(qrows) + len(qchunks)}")
    print(_BAR)

    # --- progression grouped by service ---
    print()
    _render_progression(audit_rows)

    # --- canonical landing (sampled) ---
    print()
    if cp:
        mvid = cp[0][1]
        chan = cp[0][2]
        skus = [str(r[0]) for r in cp]
        print(
            f"  Canonical — store_sku_current_position "
            f"({len(cp)} rows · mapping_version_id={mvid} · dis_channel={chan})"
        )
        if verbose or len(skus) <= _SAMPLE_N:
            print(f"    sku_id: {', '.join(skus)}")
        else:
            print(
                f"    sku_id: {', '.join(skus[:_SAMPLE_N])}  "
                f"(+{len(skus) - _SAMPLE_N} more; --verbose for all)"
            )
    else:
        print("  Canonical: none")

    # --- quarantine detail ---
    print()
    if qrows or qchunks:
        if qrows:
            print(f"  Quarantine — quarantined_rows ({len(qrows)})")
            for off, fs, fr in qrows:
                print(f"    row[{off}]  stage={fs}  reason={fr}")
        if qchunks:
            print(f"  Quarantine — quarantined_chunks ({len(qchunks)})")
            for fs, fr, rc in qchunks:
                print(f"    chunk  stage={fs}  reason={fr}  rows={rc}")
    else:
        print("  Quarantine: none")

    # --- full detail persisted (terminal samples; json loses nothing) ---
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / f"run-{trace_id}.json"
    report_path.write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "verdict": verdict,
                "current_position_rows": len(cp),
                "quarantined_chunks_count": len(qchunks),
                "quarantined_rows_count": len(qrows),
                "sku_ids": [str(r[0]) for r in cp],
                "quarantined_rows": [
                    {"row_offset": off, "failure_stage": fs, "failure_reason": fr} for off, fs, fr in qrows
                ],
                "quarantined_chunks": [
                    {"failure_stage": fs, "failure_reason": fr, "row_count_in_chunk": rc}
                    for fs, fr, rc in qchunks
                ],
                "audit": [{"service": svc, "stage": s, "outcome": o} for svc, s, o, _ts in audit_rows],
            },
            indent=2,
            default=str,
        )
    )
    print(f"\n  Full detail → {report_path}")
    return verdict


# --- preconditions ------------------------------------------------------------
def _check_preconditions(tenant: str, store_code: str, template_id: str) -> None:
    stores = rls_query(
        tenant,
        "SELECT store_id, status FROM identity_mirror.stores WHERE store_code = %s",
        (store_code,),
    )
    if not stores:
        raise SystemExit(
            f"store {store_code!r} not in identity_mirror.stores for this tenant — run: make seed"
        )
    if stores[0][1] != "ACTIVE":
        raise SystemExit(f"store {store_code!r} is {stores[0][1]}, not ACTIVE")
    tmpl = rls_query(
        tenant,
        "SELECT status FROM config.source_mappings WHERE template_id = %s::uuid AND status = 'ACTIVE'",
        (template_id,),
    )
    if not tmpl:
        raise SystemExit(f"no ACTIVE template {template_id} for this tenant — run create_template.py first")
    print(f"[B] ✓ preconditions: store {store_code} ACTIVE · template {template_id[:8]}… ACTIVE")


def main() -> int:
    ap = argparse.ArgumentParser(description="Upload a data CSV and verify landing (Script B).")
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--template", default=OUT_DIR / "template.json")
    ap.add_argument("--template-id", default=None)
    ap.add_argument("--store-code", default=None)
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--yes", action="store_true", help="skip the vary confirmation (unattended)")
    ap.add_argument("--no-stack", action="store_true")
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="show the full stack-startup block and the full sku_id list inline",
    )
    ap.add_argument("--base", default=DEFAULT_BASE_URL)
    args = ap.parse_args()

    ensure_stack(skip=args.no_stack, base=args.base, verbose=args.verbose)

    template_id, _ttype, tmpl_tenant = _resolve_template(args)
    tenant = args.tenant or tmpl_tenant or seeded_tenant_uuid()
    store_code = args.store_code or seeded_store_code()
    spec = json.loads(args.spec.read_text())

    _check_preconditions(tenant, store_code, template_id)

    ignore_key = _ignore_src_key(spec)
    varied_bytes, _delim = _vary_cell(args.csv, ignore_key, assume_yes=args.yes)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    varied_path = OUT_DIR / f"{args.csv.stem}.varied.csv"
    varied_path.write_bytes(varied_bytes)

    token = mint_tenant_token(tenant)
    url = f"{args.base}/api/v1/csv-uploads"
    resp = httpx.post(
        url,
        files={"file": (args.csv.name, varied_bytes, "text/csv")},
        data={"template_id": template_id, "store_code": store_code},
        headers=auth_header(token),
        timeout=60.0,
    )
    if resp.status_code != 201:
        print(f"[B] upload rejected: HTTP {resp.status_code} {resp.text}", file=sys.stderr)
        return 1

    trace_id = resp.json()["trace_id"]
    print(f"[B] ✓ uploaded · trace_id {trace_id} · waiting for pipeline (≤{args.timeout}s)…")

    audit_rows = _poll_audit(tenant, trace_id, args.timeout)
    verdict = _report(tenant, trace_id, audit_rows, verbose=args.verbose)
    return 0 if verdict in ("LANDED", "QUARANTINED") else 1


if __name__ == "__main__":
    raise SystemExit(main())
