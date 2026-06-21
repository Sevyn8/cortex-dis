"""Wire shape for ``POST /v1/csv-uploads`` (Slice 8, contract §8).

One response model: what the synchronous upload resolved and produced. Identity
values are the RESOLVED internal UUIDs (D37/D52) plus the readable codes; the
``upload_id`` is the deterministic ``us_`` lineage id that fills the
``csv.received`` ``upload_session_id`` role (the worker's D58 idempotency
component) — returned so the caller can correlate a retry with its original.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CsvUploadResult(BaseModel):
    """The 201 body: the accepted upload's resolved identity and pointers."""

    model_config = ConfigDict(frozen=True)

    trace_id: UUID
    upload_id: str  # the us_ lineage id (csv.received upload_session_id)
    tenant_id: UUID
    store_id: UUID
    store_code: str
    source_id: str  # derived from the template's lineage, never from the request
    template_id: UUID
    gcs_uri: str
    row_count: int  # tier-0 observed data rows (excluding the header)
    received_ts: datetime
    status: Literal["received"]
