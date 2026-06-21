"""Unit tests for the Phase-1 BqClient stub."""

from __future__ import annotations

import ast
import inspect

import pytest

import dis_core.bq as bq_module
from dis_core.bq import BqClient
from dis_core.ids import new_uuid7


def test_bqclient_constructs_without_io() -> None:
    client = BqClient(project="ithina-dis-dev", dataset="canonical_history")
    assert client.project == "ithina-dis-dev"
    assert client.dataset == "canonical_history"


def test_query_seam_raises_not_implemented() -> None:
    client = BqClient()
    with pytest.raises(NotImplementedError):
        client.query("SELECT 1", tenant_id=new_uuid7())


def test_module_does_not_import_google_bigquery() -> None:
    # Inert stub: no actual import of google-cloud-bigquery in Phase 1. Inspect
    # import statements via AST (the docstring legitimately names the package).
    tree = ast.parse(inspect.getsource(bq_module))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any("google" in name for name in imported), imported
