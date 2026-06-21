"""Slice 8a (D71): the template-keyed active-mapping lookup, proven live.

The core D71 property: with TWO ACTIVE templates under one source — the exact
state the pre-8a lookup resolved by ``.first()`` luck — each ``ingress.ready``
resolves to ITS OWN template's ``mapping_rules``, proven by distinguishable
rules (the ``currency`` derive-constant) and the per-template
``mapping_version_id`` stamp on the canonical rows (D22 unchanged: the stamp is
still the loaded mapping's version, now the RIGHT mapping's). A ``template_id``
naming no ACTIVE row fails loud with ``MAPPING_CONFIG_INVALID`` — since Slice 11a
held in quarantine and acked (the deterministic allowlist), never a silent
wrong-mapping. The ``MAPPING_LOOKED_UP`` audit ``event_data`` carries the
template the lookup keyed on (additive).

The template_id-ABSENT case is deliberately NOT here: it is structurally
unreachable at the lookup — the contract requires the field, so an absent value
fails the envelope parse (terminal ack) before any pipeline stage runs; the
proof lives in unit ``test_envelope.py`` (the required-field reject + the
required-set drift guard). No fallback code exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from dis_core.ids import new_uuid7
from dis_testing.fixtures import PRIMARY_TENANT
from streaming_consumer.orchestrate import ConsumerPipeline

from .conftest import SALE_SOURCE_ID, Cleanup, sale_csv, seed_chunk, seed_hot_row, ts

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import Engine

    from dis_storage.client import StorageClient

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mappings"


def _unique_sku(prefix: str) -> str:
    return f"{prefix}-{new_uuid7().hex[:10]}"


def _alt_sale_rules() -> dict[str, object]:
    """The sale fixture rules with a DISTINGUISHABLE difference: currency EUR.

    Same rename/targets (routes to the same event model); only the
    derive-constant differs, so the applied template is observable on every
    written row.
    """
    rules: dict[str, object] = json.loads((_FIXTURES / "sale_pos_v1.json").read_text())
    derive = rules["derive"]
    assert isinstance(derive, dict)
    derive["currency"] = [{"op": "constant", "args": {"value": "EUR"}}]
    return rules


def _seed_alt_active_template(dis_admin: Engine) -> tuple[UUID, int]:
    """Seed a SECOND ACTIVE template under the sale source; returns (id, version).

    Exactly the state D71's hard gate guarded against shipping before 8a:
    uq_csm_active_per_source permits one ACTIVE per template, so this row
    coexists with the 'default' template's ACTIVE row. activated_at satisfies
    ck_csm_activated_consistency; trg_csm_set_version_seq assigns the seq.
    """
    template_id = new_uuid7()
    with dis_admin.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO config.source_mappings "
                "(tenant_id, source_id, template_id, template_name, template_type, status, "
                " mapping_rules, activated_at) "
                "VALUES (CAST(:tenant_id AS uuid), :source_id, CAST(:template_id AS uuid), "
                " 'alt', 'sales', 'ACTIVE', CAST(:rules AS JSONB), NOW()) "
                "RETURNING mapping_version_id"
            ),
            {
                "tenant_id": str(PRIMARY_TENANT.uuid),
                "source_id": SALE_SOURCE_ID,
                "template_id": str(template_id),
                "rules": json.dumps(_alt_sale_rules()),
            },
        ).one()
    return template_id, int(row.mapping_version_id)


def _drop_alt_template(dis_admin: Engine, mapping_version_id: int, skus: list[str]) -> None:
    """Remove the alt template and every row pinning its version (FK order)."""
    with dis_admin.begin() as conn:
        conn.execute(
            text("DELETE FROM canonical.store_sku_sale_events WHERE mapping_version_id = :v"),
            {"v": mapping_version_id},
        )
        conn.execute(
            text("DELETE FROM canonical.store_sku_current_position WHERE sku_id = ANY(:skus)"),
            {"skus": skus},
        )
        conn.execute(
            text("DELETE FROM config.source_mappings WHERE mapping_version_id = :v"),
            {"v": mapping_version_id},
        )


async def test_two_active_templates_each_resolve_their_own_rules(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    """The core D71 property: the named template's rules apply, not .first() luck."""
    default_version = consumer_mappings[SALE_SOURCE_ID]
    sku_default = _unique_sku("TL-DEF")
    sku_alt = _unique_sku("TL-ALT")
    # Seed the second ACTIVE template LAST, immediately before the guarded body,
    # so any raise after this line reaches the finally and the row never leaks.
    alt_template_id, alt_version = _seed_alt_active_template(dis_admin)
    try:
        seed_hot_row(dis_admin, cleanup, sku_id=sku_default, mapping_version_id=default_version)
        seed_hot_row(dis_admin, cleanup, sku_id=sku_alt, mapping_version_id=default_version)

        # One chunk naming each template (default resolved by seed_chunk; alt explicit).
        chunk_default = seed_chunk(
            dis_admin,
            storage,
            cleanup,
            csv_data=sale_csv([(ts(0), sku_default, "1", "9.99", "9.50", "T-TLD", "1")]),
            source_id=SALE_SOURCE_ID,
            bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
        )
        chunk_alt = seed_chunk(
            dis_admin,
            storage,
            cleanup,
            csv_data=sale_csv([(ts(5), sku_alt, "1", "9.99", "9.50", "T-TLA", "1")]),
            source_id=SALE_SOURCE_ID,
            bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
            template_id=alt_template_id,
        )
        assert chunk_default.event.template_id != alt_template_id  # genuinely two templates

        assert (await pipeline.process(chunk_default.event)).disposition == "written"
        assert (await pipeline.process(chunk_alt.event)).disposition == "written"

        with dis_admin.begin() as conn:
            rows = {
                str(r.trace_id): r
                for r in conn.execute(
                    text(
                        "SELECT trace_id, mapping_version_id, currency "
                        "FROM canonical.store_sku_sale_events WHERE trace_id = ANY(:traces)"
                    ),
                    {"traces": [chunk_default.trace_id, chunk_alt.trace_id]},
                ).all()
            }
            looked_up = {
                str(r.trace_id): r.event_data
                for r in conn.execute(
                    text(
                        "SELECT trace_id, event_data FROM audit.events "
                        "WHERE stage = 'MAPPING_LOOKED_UP' AND outcome = 'SUCCESS' "
                        "AND trace_id = ANY(:traces)"
                    ),
                    {"traces": [chunk_default.trace_id, chunk_alt.trace_id]},
                ).all()
            }

        # Each chunk carries ITS OWN template's version stamp (D22) and rules effect.
        default_row = rows[str(chunk_default.trace_id)]
        alt_row = rows[str(chunk_alt.trace_id)]
        assert default_row.mapping_version_id == default_version
        assert default_row.currency == "USD"
        assert alt_row.mapping_version_id == alt_version
        assert alt_row.currency == "EUR"

        # MAPPING_LOOKED_UP event_data names the template the lookup keyed on.
        assert looked_up[str(chunk_default.trace_id)]["template_id"] == str(chunk_default.event.template_id)
        assert looked_up[str(chunk_alt.trace_id)]["template_id"] == str(alt_template_id)
        assert looked_up[str(chunk_alt.trace_id)]["source_id"] == SALE_SOURCE_ID
    finally:
        _drop_alt_template(dis_admin, alt_version, [sku_default, sku_alt])


async def test_unknown_template_raises_clean_mapping_config_error(
    pipeline: ConsumerPipeline,
    dis_admin: Engine,
    storage: StorageClient,
    cleanup: Cleanup,
    stack_env: dict[str, str],
    consumer_mappings: dict[str, int],
) -> None:
    """A template_id naming no ACTIVE row fails loud — never a silent wrong-mapping.

    Since Slice 11a the loud failure is HELD, not re-raised: MAPPING_CONFIG_INVALID
    is on the deterministic allowlist, so the chunk lands in quarantined_chunks and
    the disposition acks (the storm fix). Loudness is unchanged — the FAILURE audit
    still carries the template id, and nothing reaches canonical.
    """
    ghost_template = new_uuid7()  # no ACTIVE row anywhere carries this id
    chunk = seed_chunk(
        dis_admin,
        storage,
        cleanup,
        csv_data=sale_csv([(ts(0), _unique_sku("TL-NO"), "1", "9.99", "9.50", "T-TLN", "1")]),
        source_id=SALE_SOURCE_ID,
        bronze_bucket=stack_env["GCS_BUCKET_BRONZE"],
        template_id=ghost_template,
    )

    outcome = await pipeline.process(chunk.event)
    assert outcome.disposition == "quarantined"

    # The FAILURE landed at the lookup stage with the template in the message
    # (code-quality rule 5); the chunk is held; nothing reached canonical.
    with dis_admin.begin() as conn:
        failure = conn.execute(
            text(
                "SELECT failure_code, failure_message, data_ingress_event_id FROM audit.events "
                "WHERE trace_id = CAST(:t AS uuid) AND stage = 'MAPPING_LOOKED_UP' "
                "AND outcome = 'FAILURE'"
            ),
            {"t": str(chunk.trace_id)},
        ).one()
        # The lookup precedes the dual-write: NOTHING may land for this trace —
        # neither event table nor the hot table (no partial write).
        written = {
            table: conn.execute(
                text(
                    f"SELECT COUNT(*) FROM canonical.{table} "  # noqa: S608 - fixed identifiers
                    "WHERE trace_id = CAST(:t AS uuid)"
                ),
                {"t": str(chunk.trace_id)},
            ).scalar_one()
            for table in (
                "store_sku_sale_events",
                "store_sku_change_events",
                "store_sku_current_position",
            )
        }

        held = conn.execute(
            text(
                "SELECT status, failure_reason FROM quarantine.quarantined_chunks "
                "WHERE trace_id = CAST(:t AS uuid)"
            ),
            {"t": str(chunk.trace_id)},
        ).one()

    # Slice 30b: the stable vocabulary replaces the exception class name, and the
    # catch-all carries the bronze id it knows (the lookup runs AFTER the fetch).
    assert failure.failure_code == "MAPPING_CONFIG_INVALID"
    assert failure.data_ingress_event_id is not None
    assert str(ghost_template) in failure.failure_message
    # Slice 11a: the deterministic lookup failure is held (status=NEW) and acked.
    assert (held.status, held.failure_reason) == ("NEW", "MAPPING_CONFIG_INVALID")
    assert written == {
        "store_sku_sale_events": 0,
        "store_sku_change_events": 0,
        "store_sku_current_position": 0,
    }
