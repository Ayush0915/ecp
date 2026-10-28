import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from evolve.config import init_repo

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "tests" / "fixtures"


def make_repo(dest, source="inventory_app", mock=True):
    """A fresh git repo made from a small sample project in tests/fixtures (the AI is replaced by a script when mock=True)."""
    repo = Path(dest)
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(DEMO / source, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    g = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@t", *a], check=True, capture_output=True)
    g("init", "-q", "-b", "main"); g("add", "-A"); g("commit", "-q", "-m", "init")
    init_repo(repo)
    p = repo / ".evolution" / "config.yaml"
    c = yaml.safe_load(p.read_text()); c["test_command"] = f"{sys.executable} -m pytest -q"
    if mock:
        c["models"]["provider"] = "mock"; c["pi"]["backend"] = "scripted"
    p.write_text(yaml.safe_dump(c))
    return repo


@pytest.fixture
def demo_repo(tmp_path):
    """A fresh copy of the inventory sample project, as a git repo with .evolution set up and a scripted AI."""
    return make_repo(tmp_path / "repo")


def scenario(name):
    return json.loads((DEMO / "scenarios" / f"{name}.json").read_text())
