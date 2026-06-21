"""dis-rls guard logic, unit-tested without a database (AC3 negative cases).

The posture check is pure, so the wrong-target and bypass-role rejections are proven
here directly; the integration test proves the positive path against the live DB.
"""

from __future__ import annotations

import pytest

from dis_core.errors import DisError, RlsContextError
from dis_rls.session import _check_posture, create_rls_engine


def test_rls_error_is_dis_error_rooted() -> None:
    # AC7: dis-rls raises only DisError-rooted errors (no raw RuntimeError/ValueError).
    assert issubclass(RlsContextError, DisError)


def test_posture_accepts_dis_db_with_nobypass_role() -> None:
    # The good case: DIS database, NOSUPERUSER, NOBYPASSRLS — does not raise.
    _check_posture(database="ithina_dis_db", role="ithina_dis_user", rolsuper=False, rolbypassrls=False)


def test_posture_rejects_customer_master_database() -> None:
    # CM-shaped target (different DB name) is refused — wrong target made impossible.
    with pytest.raises(RlsContextError) as exc:
        _check_posture(
            database="ithina_platform_db",
            role="dis_mirror_reader",
            rolsuper=False,
            rolbypassrls=False,
        )
    assert exc.value.database == "ithina_platform_db"


def test_posture_rejects_bypassrls_role() -> None:
    with pytest.raises(RlsContextError):
        _check_posture(database="ithina_dis_db", role="ithina_dis_admin", rolsuper=False, rolbypassrls=True)


def test_posture_rejects_superuser_role() -> None:
    with pytest.raises(RlsContextError):
        _check_posture(database="ithina_dis_db", role="ithina_dis_admin", rolsuper=True, rolbypassrls=False)


def test_create_engine_requires_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # No silent default for a required value (code-quality rule 4).
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(RlsContextError):
        create_rls_engine()
