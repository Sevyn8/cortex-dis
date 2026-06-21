"""Trust-boundary scope proofs (AC2 / D54): no Identity Service dependency, no
trace_id minting surface, anywhere in the worker package.

A test cannot prove a behaviour's absence in general; what IS expressible is the
import graph and the source surface, both asserted here. The behavioural half (the
emitted trace equals the event's while the minting function explodes) lives in
``test_pipeline.py``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import csv_ingest_worker

_PACKAGE_DIR = Path(str(csv_ingest_worker.__file__)).parent
_SOURCES = sorted(_PACKAGE_DIR.glob("*.py"))


def test_package_has_sources() -> None:
    assert len(_SOURCES) >= 8  # the collection guard for the scans below


def _imports_and_names(source: Path) -> tuple[set[str], set[str]]:
    """(imported module paths, every Name/Attribute identifier) — docstrings excluded."""
    tree = ast.parse(source.read_text())
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return modules, names


def test_no_module_imports_identity_service_surface() -> None:
    # D54: the worker holds NO Identity Service dependency — no resolve call, no
    # client import, no external-to-internal translation (that is Slice 13).
    # AST-based so prose (docstrings stating this very invariant) cannot trip it.
    for source in _SOURCES:
        modules, names = _imports_and_names(source)
        assert not any(m.startswith("dis_core.identity") for m in modules), (
            f"{source.name} imports dis_core.identity (D54 violation)"
        )
        for forbidden in ("resolve_from_upload", "resolve_from_token"):
            assert forbidden not in names, f"{source.name} references {forbidden!r} (D54 violation)"


def test_no_module_references_the_trace_mint() -> None:
    # hard rule 4: the worker reads trace_id off the event; new_trace_id must not
    # be imported or referenced in code. (new_uuid7 for the bronze row id is
    # sanctioned — an id, not a trace.)
    for source in _SOURCES:
        _, names = _imports_and_names(source)
        assert "new_trace_id" not in names, f"{source.name} references the trace mint"


def test_importing_the_worker_loads_no_identity_module() -> None:
    # In a fresh interpreter, importing every worker module must not pull in
    # dis_core.identity (the import graph IS the dependency).
    code = (
        "import sys;"
        "import csv_ingest_worker.main, csv_ingest_worker.pipeline,"
        " csv_ingest_worker.subscriber, csv_ingest_worker.publisher,"
        " csv_ingest_worker.bronze, csv_ingest_worker.preflight,"
        " csv_ingest_worker.pii_gate, csv_ingest_worker.audit,"
        " csv_ingest_worker.envelope, csv_ingest_worker.config;"
        "assert 'dis_core.identity' not in sys.modules,"
        " sorted(m for m in sys.modules if m.startswith('dis_core'))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
