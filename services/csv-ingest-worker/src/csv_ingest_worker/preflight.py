"""The DuckDB structural preflight (D13 permissive, D16) — DuckDB is contained HERE.

Structural only: does the object parse as CSV, is a header present, plausible
structure (>= 1 column), row count, type sniff. NO column- or mapping-aware checks —
those are the source-shape suite's (Slice 10). Parse-as-CSV is mechanism the sniff
needs, not the tier-0 policy gate (that is dis-ui-server's upload endpoint, D51).

Pinned-dependency containment (the Slice 5 pattern): this module is the only place
DuckDB is imported, and the behaviours it relies on — ``sniff_csv`` prepared-parameter
binding, ``Columns``/``HasHeader`` result shape, header detection, and
``duckdb.Error`` on unparseable input — are asserted by the canary tests in
``tests/unit/test_preflight.py``, so a DuckDB version bump that changes them fails
the canary, not production.

Failure detail NEVER carries DuckDB's message verbatim: DuckDB errors can quote file
content (cell values), and the root rule is never log PII or raw payloads. The typed
error carries a stable ``reason`` code and the exception class name only.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb

from dis_core.errors import PreflightFailedError


@dataclass(frozen=True)
class PreflightResult:
    """What the structural sniff learned about the object (metadata only)."""

    columns: tuple[str, ...]
    column_types: tuple[str, ...]
    row_count: int
    size_bytes: int
    # The single-character field separator DuckDB's sniff_csv detected (Slice 16f).
    # Carried onto ingress.ready so the consumer parses with the right delimiter
    # instead of a hardcoded comma. sniff_csv exposes no confidence signal, so the
    # detected value is carried as-is (the locked decision); a wrong delimiter fails
    # loudly at the consumer's mapping gate rather than corrupting silently.
    delimiter: str


def run_preflight(data: bytes, *, tenant_id: str, trace_id: str) -> PreflightResult:
    """Run the structural preflight over the downloaded object bytes.

    Returns the sniffed structure, or raises :class:`PreflightFailedError` with a
    stable ``reason``: ``not_csv`` (does not parse), ``no_header`` (the sniffer
    detected a headerless file — downstream PII detection and mapping are
    column-name based), ``no_columns``, or ``no_data_rows`` (header but zero rows;
    nothing to ingest). Performs no writes; reads only the temp copy of ``data``.
    """
    if not data.strip():
        # Zero-byte / whitespace-only object (the named edge case). Guarded HERE
        # because DuckDB does not raise on an empty file — it fabricates a
        # 'column0' headerless sniff (canary-pinned), which would misreport the
        # failure as no_header.
        raise PreflightFailedError(
            "structural preflight failed: object is empty",
            reason="not_csv",
            detail="empty object",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )

    with tempfile.TemporaryDirectory(prefix="csv-preflight-") as tmp:
        path = str(Path(tmp) / "object.csv")
        Path(path).write_bytes(data)
        con = duckdb.connect()
        try:
            try:
                sniff = con.execute(
                    "SELECT Delimiter, HasHeader, Columns FROM sniff_csv(?)", [path]
                ).fetchone()
                if sniff is None:  # pragma: no cover - sniff_csv always yields one row
                    raise PreflightFailedError(
                        "structural preflight failed: sniff returned no result",
                        reason="not_csv",
                        tenant_id=tenant_id,
                        trace_id=trace_id,
                    )
                delimiter, has_header, columns_raw = sniff
                row = con.execute("SELECT count(*) FROM read_csv(?)", [path]).fetchone()
                row_count = int(row[0]) if row is not None else 0
            except duckdb.Error as exc:
                # Stable reason + exception CLASS only — DuckDB messages can quote
                # file content, which must never reach a log or an error (hard rule 2).
                raise PreflightFailedError(
                    "structural preflight failed: object does not parse as CSV",
                    reason="not_csv",
                    detail=type(exc).__name__,
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                ) from exc
        finally:
            con.close()

    columns = tuple(str(col["name"]) for col in columns_raw)
    column_types = tuple(str(col["type"]) for col in columns_raw)

    if not columns:
        raise PreflightFailedError(
            "structural preflight failed: no columns sniffed",
            reason="no_columns",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
    if not has_header:
        # Headerless is structural failure here: PII detection (D40 heuristic) and
        # the Slice 10 mapping are column-NAME based; auto-generated names carry none.
        raise PreflightFailedError(
            "structural preflight failed: no header row detected",
            reason="no_header",
            detail=f"columns={len(columns)}",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )
    if row_count < 1:
        raise PreflightFailedError(
            "structural preflight failed: header but zero data rows",
            reason="no_data_rows",
            detail=f"columns={len(columns)}",
            tenant_id=tenant_id,
            trace_id=trace_id,
        )

    return PreflightResult(
        columns=columns,
        column_types=column_types,
        row_count=row_count,
        size_bytes=len(data),
        delimiter=str(delimiter),
    )
