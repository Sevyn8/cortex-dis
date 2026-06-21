"""Pipeline stages (one concern per module, code-quality rule 7).

``fetch`` → ``mapping`` (load + route) → ``validate_pre`` → engine apply (via
``mapping``) → ``validate_post`` → ``normalize`` (write-shape) → sinks. Per-row
branching (``branch.py``) deliberately does not exist in v1.0: routing is
mapping-load-time (one chunk targets one event model); the per-row-versus-chunk
split arrives with quarantine in Slice 11.
"""

from __future__ import annotations
