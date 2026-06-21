"""Shared upsert logic for identity_mirror.

Per-entity mapping + conditional upsert SQL. Upsert-only: insert + update on the
natural keys, **never delete, never soft-delete**; lifecycle is Customer Master's
``status`` replicated verbatim. The conditional ``DO UPDATE ... WHERE <existing IS
DISTINCT FROM excluded>`` makes a no-change re-run a true no-op (idempotence): an
unchanged row is not rewritten and its ``mirror_synced_at`` is preserved.

``RETURNING (xmax = 0) AS inserted`` classifies the row: a freshly inserted row has
``xmax = 0`` (true); a row updated by the conflict path has ``xmax != 0`` (false); an
unchanged row (the WHERE excluded the update) returns **no row** at all.
"""
