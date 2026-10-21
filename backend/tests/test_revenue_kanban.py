"""Revenue module + Revenue Kanban regression (real calculation code, in-memory MongoDB)."""
import importlib.util
import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "revenue_e2e_script.py")


@pytest.mark.skipif(
    importlib.util.find_spec("mongomock_motor") is None or importlib.util.find_spec("httpx") is None,
    reason="pip install -r requirements-dev.txt (mongomock-motor, httpx) to run the in-memory Revenue E2E",
)
def test_revenue_and_kanban_end_to_end():
    proc = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, timeout=300,
                          cwd=os.path.dirname(os.path.dirname(__file__)))
    out = proc.stdout + proc.stderr
    failures = [line for line in out.splitlines() if line.startswith("FAIL")]
    assert proc.returncode == 0 and not failures and "ALL OK" in out, "\n".join(failures) or out[-3000:]
