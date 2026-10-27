import json
from pathlib import Path

import pytest

from evolve.scope import check_path

CASES = json.loads((Path(__file__).parent / "scope_cases.json").read_text())


@pytest.mark.parametrize("c", CASES, ids=[c["path"] for c in CASES])
def test_scope_cases(c):
    ok, _ = check_path(c["path"], c["allowed"], c["forbidden"])
    assert ok == c["ok"]
