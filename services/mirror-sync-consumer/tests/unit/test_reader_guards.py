"""Target + platform-context guards — the always-run, non-skip proofs (criteria 4 & 5).

These are pure functions (no DB), so they cannot skip on an absent dependency: the
fail-loud behavior is asserted unconditionally, exactly as the slice requires for the
zero-row-read trap and the target mix-up.
"""

from __future__ import annotations

import pytest

from dis_core.errors import CustomerMasterReadError
from mirror_sync_consumer.pull.reader import assert_cm_target, assert_platform_context

_CM = "ithina_platform_db"
_DIS = "ithina_dis_db"


def test_target_guard_refuses_the_dis_database() -> None:
    with pytest.raises(CustomerMasterReadError):
        assert_cm_target(database=_DIS, role="x", expected_cm_db=_CM, dis_db=_DIS)


def test_target_guard_requires_the_expected_cm_database() -> None:
    with pytest.raises(CustomerMasterReadError):
        assert_cm_target(database="some_other_db", role="x", expected_cm_db=_CM, dis_db=_DIS)


def test_target_guard_passes_on_customer_master() -> None:
    assert_cm_target(database=_CM, role="dis_mirror_reader", expected_cm_db=_CM, dis_db=_DIS)


@pytest.mark.parametrize("bad", [None, "", "TENANT", "platform", "PLATFORM "])
def test_context_guard_requires_platform(bad: str | None) -> None:
    with pytest.raises(CustomerMasterReadError):
        assert_platform_context(bad)


def test_context_guard_passes_on_platform() -> None:
    assert_platform_context("PLATFORM")
