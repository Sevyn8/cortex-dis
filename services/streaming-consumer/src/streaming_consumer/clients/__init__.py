"""Transport clients. ``identity.py`` deliberately does not exist in Slice 10:
identity arrives resolved on ``ingress.ready`` (D54 trust model) and existence is
enforced by the composite FK (D39); the real Identity Service is Slice 13 (D28).
"""

from __future__ import annotations
