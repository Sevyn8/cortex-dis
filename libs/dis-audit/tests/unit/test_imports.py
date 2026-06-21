"""Dependency direction (AC1): dis-audit depends on dis-core + dis-rls only.

A fresh interpreter check: importing dis_audit must not pull dis_mapping / dis_validation /
dis_canonical (no Slice 5 surface; mapping_version_id is a value the caller supplies, not a
code dependency). dis_rls IS expected (the chosen write posture, decisions.md D43).
"""

from __future__ import annotations

import subprocess
import sys


def test_dis_audit_does_not_depend_on_slice5_or_canonical() -> None:
    probe = (
        "import sys\n"
        "import dis_audit\n"
        "for forbidden in ('dis_mapping', 'dis_validation', 'dis_canonical'):\n"
        "    assert forbidden not in sys.modules, f'dis_audit must not depend on {forbidden}'\n"
        "assert 'dis_rls' in sys.modules, 'dis_audit write posture goes through dis_rls'\n"
        "print('clean')\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout
