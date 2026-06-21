"""Sinks: the canonical dual-write and the fire-and-forget audit emitter.

``quarantine.py`` and ``dlq.py`` deliberately do not exist in Slice 10: the
quarantine publish is Slice 11; the ``pipeline.dlq`` backpressure pattern is D27,
carried forward.
"""

from __future__ import annotations
