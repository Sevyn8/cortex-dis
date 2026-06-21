"""dis-enrichment is pure and decoupled (slice-5b): importing it must not pull in the
sibling pipeline libs, the I/O-bearing DIS libs, or any DB/GCP client.

Checked in a FRESH interpreter (subprocess; pattern from dis-mapping): the shared
pytest process already has sqlalchemy etc. loaded by other libs' tests, so an
in-process sys.modules check would be meaningless. This test ERRORS (nonzero exit /
failed assert), it never skips.
"""

from __future__ import annotations

import subprocess
import sys

# NOTE: the bare "google" NAMESPACE package is pre-registered by `import polars`
# (an installed-namespace artifact, no client code) — the meaningful runtime
# assertion is that no GCP CLIENT module loads. Source-level `import google.*`
# is separately forbidden by the import-linter contract (root pyproject).
FORBIDDEN = (
    "dis_mapping",
    "dis_validation",
    "dis_canonical",
    "dis_rls",
    "dis_pii",
    "dis_storage",
    "dis_audit",
    "dis_quarantine",
    "dis_testing",
    "sqlalchemy",
    "psycopg",
    "google.cloud.storage",
    "google.cloud.bigquery",
    "google.cloud.pubsub_v1",
    "httpx",
)


def test_importing_dis_enrichment_loads_no_io_or_sibling_modules() -> None:
    probe_lines = ["import sys", "import dis_enrichment, dis_enrichment.engine, dis_enrichment.registry"]
    probe_lines += [
        f"assert {name!r} not in sys.modules, 'dis_enrichment must not pull in {name}'" for name in FORBIDDEN
    ]
    probe_lines.append("print('clean')")
    result = subprocess.run(
        [sys.executable, "-c", "\n".join(probe_lines)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
