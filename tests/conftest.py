import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from evolve.config import init_repo

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"


@pytest.fixture
def demo_repo(tmp_path):
    """A fresh copy of the inventory demo app, as a git repo with .evolution set up."""
    repo = tmp_path / "repo"
    shutil.copytree(DEMO / "inventory_app", repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    g = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@t", *a], check=True, capture_output=True)
    g("init", "-q", "-b", "main"); g("add", "-A"); g("commit", "-q", "-m", "init")
    init_repo(repo)
    p = repo / ".evolution" / "config.yaml"
    c = yaml.safe_load(p.read_text()); c["test_command"] = f"{sys.executable} -m pytest -q"; p.write_text(yaml.safe_dump(c))
    return repo


def scenario(name):
    return json.loads((DEMO / "scenarios" / f"{name}.json").read_text())
