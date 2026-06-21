"""Minimal, test-only exceptions for dis-testing.

These descend from the shared ``dis_core.errors.DisError`` root (Slice 3), so the
whole DIS error tree is single-rooted, but the test-specific leaves stay here:
``TestInfraError`` / ``FixtureError`` / ``SeedError`` are test-infra concepts and
must not leak into production ``dis-core``. The dependency direction is
``dis-testing -> dis-core`` (allowed); ``dis-core`` never imports ``dis-testing``.

dis-testing is test infrastructure only — never imported by production code.
"""

from __future__ import annotations

from dis_core.errors import DisError


class TestInfraError(DisError):
    """Base for all dis-testing failures."""


class FixtureError(TestInfraError):
    """A fixture-truth lookup failed (unknown code, inconsistent set)."""


class SeedError(TestInfraError):
    """The fixture seeder could not complete a write."""
