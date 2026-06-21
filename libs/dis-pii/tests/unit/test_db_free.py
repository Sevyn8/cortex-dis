"""dis-pii is DB-free and crypto-free (AC1, AC5): importing it must not pull in
sqlalchemy, dis_rls, or any crypto/network stack.

Checked in a FRESH interpreter (subprocess): the shared pytest process already has
sqlalchemy/dis_rls loaded by other libs' tests, so an in-process sys.modules check
would be meaningless. A clean subprocess reflects how a real consumer imports dis_pii.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_dis_pii_loads_no_db_or_rls() -> None:
    probe = (
        "import sys\n"
        "import dis_pii, dis_pii.detectors, dis_pii.gate\n"
        "assert 'dis_rls' not in sys.modules, 'dis_pii must not depend on dis_rls'\n"
        "assert 'sqlalchemy' not in sys.modules, 'dis_pii must not touch the DB layer'\n"
        "print('clean')\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
