import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_guard_rules_match_python_rules():
    r = subprocess.run(["node", str(ROOT / "guard" / "test-core.mjs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_guard_extension_blocks_like_pi_would():
    """Loads guard/guard.ts the way Pi does. Skipped if Pi (which ships jiti) is not installed."""
    import os
    r = subprocess.run(["node", str(ROOT / "guard" / "test-hook.mjs")], capture_output=True, text=True, env=os.environ)
    if r.stdout.startswith("SKIP"):
        pytest.skip(r.stdout.strip())
    assert r.returncode == 0, r.stdout + r.stderr
