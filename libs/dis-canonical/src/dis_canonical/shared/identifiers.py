"""Identifier aliases for canonical models — re-exported from dis-core.

The canonical tables key by the **internal UUID** (``identity_mirror`` /
``canonical.*`` PKs), so models reuse the single dis-core definition rather than
redefining it. See the dis-core ``identifiers`` module (and dis-core CLAUDE.md)
for the name-collision warning with the identity contract's external ``t_*``/``s_*``
string aliases (D37, OPEN).
"""

from __future__ import annotations

from dis_core.identifiers import MappingVersionId, StoreId, TenantId, TraceId

__all__ = ["MappingVersionId", "StoreId", "TenantId", "TraceId"]
