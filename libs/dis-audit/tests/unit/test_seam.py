"""The BigQuery writer is an inert seam (AC5): import-safe, no I/O, no google-cloud-bigquery.

Mirrors dis-pii/test_seams.py + test_db_free.py: construction does no I/O, the method is an
unimplemented seam, and importing dis_audit pulls in no BigQuery client (hard rule 8 — the
only BQ access is via the dis-core BqClient stub, which itself imports no google SDK).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from dis_audit import AuditBackend, BigQueryAuditWriter, select_writer
from dis_core.bq import BqClient
from dis_core.errors import AuditWriteError


def test_bigquery_writer_constructs_without_io() -> None:
    assert BigQueryAuditWriter() is not None
    assert BigQueryAuditWriter(BqClient(project="p", dataset="d")) is not None


async def test_bigquery_write_is_an_unimplemented_seam() -> None:
    with pytest.raises(NotImplementedError):
        await BigQueryAuditWriter().write(event=None)  # type: ignore[arg-type]


def test_select_writer_bigquery_returns_inert_seam() -> None:
    assert isinstance(select_writer(AuditBackend.BIGQUERY), BigQueryAuditWriter)


def test_select_writer_postgres_requires_engine() -> None:
    # No silent fallback for a required value (code-quality rule 4); DisError-rooted, not ValueError.
    with pytest.raises(AuditWriteError):
        select_writer(AuditBackend.POSTGRES)


def test_importing_dis_audit_loads_no_bigquery_sdk() -> None:
    # A fresh interpreter: importing dis_audit must not pull google-cloud-bigquery (the
    # Phase-3 seam is inert). sqlalchemy IS expected (the Cloud SQL writer needs it).
    probe = (
        "import sys\n"
        "import dis_audit, dis_audit.writer, dis_audit.bigquery_writer\n"
        "assert 'google.cloud.bigquery' not in sys.modules, 'dis_audit must not import the BQ SDK'\n"
        "print('clean')\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
