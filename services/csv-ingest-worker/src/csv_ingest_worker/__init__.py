"""csv-ingest-worker — CSV upload Phase-2 worker (Slice 9b, D36/D54).

Triggered by the ``csv.received`` event dis-ui-server publishes once a tenant's
signed-PUT upload is confirmed saved in GCS. The worker TRUSTS the event (D54): it
reads the resolved internal identity (UUID ``tenant_id``/``store_id``) and the
``trace_id`` off the envelope, calls no Identity Service, and mints no ``trace_id``.

Per event: cross-check the GCS path against the event identity (D53), DuckDB
structural preflight (D13/D16), the dis-pii fail-loud gate (hard rule 2, D40),
one metadata-only bronze row via dis-rls (hard rules 1 & 12), then — only on
preflight success — the frozen ``ingress.ready`` publish (write-then-conditionally-
publish, D5). Idempotent: same content hash + upload session + tenant within 24h
returns the prior ``trace_id`` (resume-and-mark semantics, D59).
"""

from __future__ import annotations
