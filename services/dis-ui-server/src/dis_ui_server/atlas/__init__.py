"""Atlas console (A4) BFF: draft store + the upload/ratify/publish handlers.

The console surfaces (PR3) live in dis-ui; this is the correctness core in the BFF
(PR1): the DraftStore interface (in-memory here; the real platform-scoped table is
PR2), and the handlers that assemble a draft IR from uploaded CSVs (the A3 path),
ratify it, and publish-FREEZE it through the single ratify gate. Publish freezes the
immutable versioned IR and writes an audit event; A1 code generation is out-of-band
(freeze-not-generate).
"""

from __future__ import annotations

from dis_ui_server.atlas.store import (
    DraftStore,
    DraftSummary,
    InMemoryDraftStore,
    PostgresDraftStore,
)

__all__ = ["DraftStore", "DraftSummary", "InMemoryDraftStore", "PostgresDraftStore"]
