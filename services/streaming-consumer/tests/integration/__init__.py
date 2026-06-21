"""Package context so the integration conftest helpers import typed under mypy --strict
(per-package gate). Deviation from the no-__init__ test convention, recorded in the
Slice 10 inventory: pytest importlib mode and the per-package mypy run are unaffected.
"""
