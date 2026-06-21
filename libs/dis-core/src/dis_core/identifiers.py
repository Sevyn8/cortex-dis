"""Internal data-plane identifier vocabulary.

The DIS data plane keys every canonical, RLS, audit, and storage path by the
**internal 128-bit UUID** that ``identity_mirror`` and ``canonical.*`` use as
primary keys. These aliases name that vocabulary in one place so the data-plane
libs (``dis-rls``, ``dis-pii``, ``dis-storage``, ``dis-audit``) and
``dis-canonical`` share one definition instead of redefining it each.

WARNING — name collision with the identity contract (latent D37 split):
``dis_core.identity.models`` also defines ``TenantId`` / ``StoreId``, but there
they are ``Annotated[str]`` for the *external* ``t_*`` / ``s_*`` Customer Master
contract ids. Here they are ``UUID`` — the *internal* keys. Same names, opposite
types, different modules. They never share an import namespace, but importing the
wrong one type-checks clean and is semantically wrong. The external<->internal
translation is unresolved (``decisions.md`` D37, OPEN, deadline Slice 7); until
then, import the UUID forms from here for anything touching the DB/RLS/canonical.
"""

from __future__ import annotations

from uuid import UUID

# Internal UUID keys (UUIDv7). NOT the external t_*/s_* contract strings.
TenantId = UUID
StoreId = UUID
TraceId = UUID

# config.source_mappings.mapping_version_id is BIGINT (decisions.md D22).
MappingVersionId = int
