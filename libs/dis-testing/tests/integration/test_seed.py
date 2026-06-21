"""Seeder integration tests (acceptance criterion 5).

Runs against the live DIS Postgres. The seeder writes ONLY
``config.source_mappings`` now — ``identity_mirror`` is owned by mirror-sync, so
each test syncs it first (the ``seeded_identity`` plugin fixture does the
sync-then-seed). Assertions are on the resulting DB state, so they hold whether
or not rows pre-existed from an earlier run.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from dis_testing import fixtures as fx
from dis_testing.errors import SeedError
from dis_testing.seed import seed_default_fixtures

pytestmark = pytest.mark.integration


def test_seed_writes_default_mapping_and_is_idempotent(seeded_identity: Engine) -> None:
    # seeded_identity has already synced identity_mirror and run the seeder once.
    dis_engine = seeded_identity

    # Re-running the seeder must insert nothing and must not raise — the
    # idempotency contract (the mapping existence-guard).
    second = seed_default_fixtures(engine=dis_engine)
    assert second.mappings_inserted == 0

    # The mirror is populated by the sync, the mapping by the seed. config.source_mappings
    # is RLS ON (Slice 14a) and dis_engine is the NOBYPASSRLS service role: the mapping
    # count must read under the fixture tenant's GUC or it returns zero rows.
    primary_uuid = fx.tenant_uuid_for(fx.PRIMARY_TENANT.display_code)
    with dis_engine.begin() as conn:
        tenants = conn.execute(text("SELECT count(*) FROM identity_mirror.tenants")).scalar_one()
        stores = conn.execute(text("SELECT count(*) FROM identity_mirror.stores")).scalar_one()
        conn.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(primary_uuid)},
        )
        active = conn.execute(
            text("SELECT count(*) FROM config.source_mappings WHERE status = 'ACTIVE'")
        ).scalar_one()

    assert tenants >= len(fx.TENANTS)
    assert stores >= len(fx.STORES)
    assert active >= 1


def test_default_mapping_version_seq_is_one(seeded_identity: Engine) -> None:
    dis_engine = seeded_identity
    tenant_uuid = fx.tenant_uuid_for(str(fx.DEFAULT_SOURCE_MAPPING["tenant_display_code"]))
    # RLS ON (Slice 14a): the read must carry the tenant GUC (NOBYPASSRLS role).
    with dis_engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_uuid)},
        )
        row = conn.execute(
            text(
                "SELECT version_seq_per_source, template_id, template_name "
                "FROM config.source_mappings "
                "WHERE tenant_id = :tid AND source_id = :sid AND status = 'ACTIVE'"
            ),
            {"tid": str(tenant_uuid), "sid": fx.DEFAULT_SOURCE_MAPPING["source_id"]},
        ).one()
    assert row.version_seq_per_source == 1
    # The Slice 14a grain: the row carries a template. Its id is the fixture
    # pin on a virgin DB but the 0005-backfill mint on a DB that pre-dates
    # 14a (the seeder existence-guard never rewrites it), so assert validity
    # plus the deterministic name — identical in both provenances.
    assert isinstance(UUID(str(row.template_id)), UUID)
    assert row.template_name == fx.DEFAULT_TEMPLATE_NAME


def test_seeded_tenant_uuid_matches_fixture_bridge(seeded_identity: Engine) -> None:
    # The bridge: a fixture external id maps to the exact UUID row mirror-sync wrote.
    dis_engine = seeded_identity
    primary_uuid = fx.tenant_uuid_for(fx.PRIMARY_TENANT.display_code)
    with dis_engine.connect() as conn:
        row = conn.execute(
            text("SELECT name, status FROM identity_mirror.tenants WHERE tenant_id = :tid"),
            {"tid": str(primary_uuid)},
        ).first()
    assert row is not None
    assert row.name == fx.PRIMARY_TENANT.name
    assert row.status == fx.PRIMARY_TENANT.status


def test_seeder_raises_when_tenant_not_mirrored(dis_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    # The FK pre-check: the seeder requires the mapping's tenant FK target to be
    # mirrored first (mirror-sync owns identity_mirror). Point the default mapping
    # at a tenant that was never synced and the seeder must RAISE, not fail the
    # final FK INSERT with an opaque IntegrityError.
    not_mirrored = UUID("019e5e3c-0000-7000-8000-00000000beef")
    monkeypatch.setattr(fx, "tenant_uuid_for", lambda _code: not_mirrored)
    with pytest.raises(SeedError):
        seed_default_fixtures(engine=dis_engine)
