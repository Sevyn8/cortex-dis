"""Test fixture seeder.

Writes the default ``config.source_mappings`` row (:mod:`dis_testing.fixtures`)
into the DIS database so later slices' tests have a default mapping to exercise
FK and RLS behaviour against.

SCOPE — read carefully:
  * **Test infrastructure only.** Never a runtime path. Runtime source-mapping
    creation is Slice 14; this seeder is the test shortcut for it.
  * **The seeder no longer writes ``identity_mirror``.** ``identity_mirror`` is
    OWNED by mirror-sync (Slice 7, DB-pull from the Customer Master stand-in) —
    the only path that populates tenants/stores, in tests as in production. The
    seeder's job is reduced to ``config.source_mappings``; the FK target for the
    mapping (the tenant in ``identity_mirror.tenants``) must already exist, so a
    sync MUST precede the seed (see :func:`dis_testing.identity_sync`). The
    seeder enforces this with a fail-loud FK pre-check.
  * **DIS database only.** It uses ``POSTGRES_URL`` (5433 / ithina_dis_db) and has
    no code path to Customer Master (5432). See the Slice 2 plan §1.
  * **Direct SQLAlchemy is intentional here.** The root rule "canonical reads/writes
    go through libs/dis-rls" is about *canonical* schemas; this writes only to
    ``config``. ``config.source_mappings`` is RLS ON since Slice 14a, so the mapping
    existence-check + INSERT set the transaction-local ``app.tenant_id`` GUC
    (the NOBYPASSRLS service role would otherwise read zero rows and fail the
    policy WITH CHECK).

Idempotency: the single ACTIVE ``config.source_mappings`` row is guarded by an
existence check (its PK is a BIGSERIAL that cannot conflict; the partial unique
index ``uq_csm_active_per_source`` backstops a duplicate ACTIVE per template).
The fixture's ``template_id`` is pinned (fixtures.py), never minted per run, so
re-running is a no-op and never raises.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text

from dis_testing import fixtures as fx
from dis_testing.errors import SeedError

_SELECT_TENANT_MIRRORED = text("SELECT 1 FROM identity_mirror.tenants WHERE tenant_id = :tenant_id LIMIT 1")

_SELECT_ACTIVE_MAPPING = text(
    """
    SELECT 1 FROM config.source_mappings
    WHERE tenant_id = :tenant_id AND source_id = :source_id AND status = 'ACTIVE'
    LIMIT 1
    """
)

_INSERT_MAPPING = text(
    """
    INSERT INTO config.source_mappings
        (tenant_id, source_id, template_id, template_name, template_type, status, mapping_rules,
         activated_at)
    VALUES
        (:tenant_id, :source_id, :template_id, :template_name, :template_type, 'ACTIVE',
         CAST(:mapping_rules AS JSONB), NOW())
    """
)

# config.source_mappings is RLS ON (FORCE, Slice 14a); the seeding role is
# NOBYPASSRLS, so the mapping check/insert need the transaction-local GUC.
_SET_TENANT_GUC = text("SELECT set_config('app.tenant_id', :tenant_id, true)")


@dataclass
class SeedSummary:
    """What the seeder did, for logging / CLI output / test assertions."""

    mappings_inserted: int = 0

    def __str__(self) -> str:
        return f"seeded: source_mappings +{self.mappings_inserted}"


def _resolve_url(url: str | None) -> str:
    resolved = url or os.environ.get("POSTGRES_URL")
    if not resolved:
        # No silent default for a required value (root CLAUDE.md error rule).
        raise SeedError("POSTGRES_URL is not set and no url was passed to the seeder")
    return resolved


def seed_default_fixtures(*, engine: Engine | None = None, url: str | None = None) -> SeedSummary:
    """Seed the default fixture set into the DIS database. Idempotent.

    Pass an ``engine`` (tests reuse one) or a ``url``; otherwise ``POSTGRES_URL`` is
    read from the environment.
    """
    own_engine = engine is None
    eng = engine or create_engine(_resolve_url(url))
    try:
        return _seed(eng)
    finally:
        if own_engine:
            eng.dispose()


def _seed(eng: Engine) -> SeedSummary:
    summary = SeedSummary()

    # Default ACTIVE source mapping (existence-guarded; BIGSERIAL PK can't
    # conflict). identity_mirror is OWNED by mirror-sync — the seeder no longer
    # writes it. The mapping's tenant FK target must already be mirrored, so a
    # sync MUST precede this seed; assert it loudly rather than fail the FINAL
    # FK INSERT with an opaque IntegrityError. RLS ON (Slice 14a): scope the
    # transaction to the fixture tenant first — without the GUC the existence
    # check reads zero rows and the INSERT fails the policy WITH CHECK.
    mapping = fx.DEFAULT_SOURCE_MAPPING
    tenant_uuid = str(fx.tenant_uuid_for(str(mapping["tenant_display_code"])))
    with eng.begin() as conn:
        conn.execute(_SET_TENANT_GUC, {"tenant_id": tenant_uuid})
        tenant_mirrored = conn.execute(_SELECT_TENANT_MIRRORED, {"tenant_id": tenant_uuid}).first()
        if tenant_mirrored is None:
            raise SeedError(
                f"identity_mirror has no tenant {tenant_uuid} — run mirror-sync before "
                "seeding config.source_mappings (sync must precede seed; fk_csm_tenant)"
            )
        exists = conn.execute(
            _SELECT_ACTIVE_MAPPING,
            {"tenant_id": tenant_uuid, "source_id": mapping["source_id"]},
        ).first()
        if exists is None:
            conn.execute(
                _INSERT_MAPPING,
                {
                    "tenant_id": tenant_uuid,
                    "source_id": mapping["source_id"],
                    "template_id": str(mapping["template_id"]),
                    "template_name": mapping["template_name"],
                    "template_type": mapping["template_type"],
                    "mapping_rules": json.dumps(mapping["mapping_rules"]),
                },
            )
            summary.mappings_inserted += 1

    return summary


def main() -> None:
    summary = seed_default_fixtures()
    print(summary)  # noqa: T201 — dev helper output


if __name__ == "__main__":
    main()
