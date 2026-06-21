"""streaming-consumer — the DIS ELT happy path (Slice 10).

Consumes ``ingress.ready``, fetches the bronze chunk, loads the active mapping,
validates (pre and post), applies the four mapping sub-stages, stamps
``mapping_version_id`` (D22), and atomically dual-writes canonical (hot upsert +
event insert in ONE Cloud SQL transaction, D30) under the event's tenant via
``dis-rls`` (hard rules 1 and 12). At-least-once delivery is absorbed by the D33
read-time dedup posture over ``(tenant_id, store_id, source_id, source_event_id)``
(D38 resolution, migration 0003), never by transactional idempotency.
"""

from __future__ import annotations
