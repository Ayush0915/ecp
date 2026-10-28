"""Per-repo settings, stored in <repo>/.evolution/config.yaml. This is what makes
the tool work for ANY repo: nothing is hard-coded to one project."""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

RULES_TEMPLATE = """# Rules for this repo

Plain-language rules that every change must follow. Elephant reads this when it
writes a plan; Goldfish checks the plan and the result against it.
Edit freely. Examples:

- Keep changes small: one problem per change.
- Never change public function names without a new ECP that says so.
- Every bug fix must come with a test that fails before the fix and passes after.
- Do not add new dependencies.
"""


class Models(BaseModel):
    """Azure OpenAI. The connection itself (base URL, key, deployment) comes from the .env file, never from this file.
    A role may name its own Azure deployment here; empty means AZURE_OPENAI_DEPLOYMENT."""
    provider: str = "azure"  # azure | mock (tests only)
    elephant: str = ""
    goldfish: str = ""
    correctness: str = ""  # a different deployment here gives the correctness reviewer a different brain from the builder

    def resolve(self, key: str) -> str:
        import os
        return getattr(self, key, "") or self.goldfish or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "") or "deployment"


class PiSettings(BaseModel):
    backend: str = "pi"  # pi (the real Pi agent) | scripted (tests only)
    sandbox: str = "none"  # none | podman  (podman = Pi runs inside a container that can only see the repo)
    image: str = "evolve-pi:latest"  # container image built by `evolve sandbox build`
    model: str = ""  # normally empty: Pi is pointed at the Azure deployment automatically
    allow_bash: bool = False  # off by default: tests are run by the control plane, not by Pi
    timeout_s: int = 900


class Config(BaseModel):
    test_command: str = ""
    python: str = ""  # interpreter of the repo's own environment (made by `evolve prepare`); empty = the one running evolve
    protected_paths: list[str] = Field(default_factory=list)  # touching these makes a change HIGH risk
    models: Models = Field(default_factory=Models)
    pi: PiSettings = Field(default_factory=PiSettings)
    max_revise_rounds: int = 3
    context_bytes: int = 150_000  # how much repo text Elephant may read
    base_branch: str = ""  # empty = whatever is checked out when a run starts
    require_fail_before: list[str] = Field(default_factory=lambda: ["bug"])  # change types that must prove "tests fail on old code"


def config_path(repo: Path) -> Path:
    return Path(repo) / ".evolution" / "config.yaml"


def load_config(repo: Path) -> Config:
    p = config_path(repo)
    if not p.exists():
        raise FileNotFoundError(f"{p} not found. Run `evolve init` in the repo first.")
    return Config(**(yaml.safe_load(p.read_text(encoding="utf-8")) or {}))


def detect_test_command(repo: Path) -> str:
    repo = Path(repo)
    if (repo / "pytest.ini").exists() or (repo / "tests").is_dir() or (repo / "pyproject.toml").exists():
        return "python -m pytest -q"
    if (repo / "package.json").exists():
        return "npm test --silent"
    if (repo / "go.mod").exists():
        return "go test ./..."
    if (repo / "Cargo.toml").exists():
        return "cargo test"
    return ""


def init_repo(repo: Path) -> dict:
    """Create .evolution/ with sensible defaults. Safe to re-run (never overwrites)."""
    repo = Path(repo)
    if not (repo / ".git").exists():
        raise RuntimeError("This folder is not a git repo. Evolution needs git to roll back safely (run `git init`).")
    evo = repo / ".evolution"
    for sub in ("ecps", "evidence"):
        (evo / sub).mkdir(parents=True, exist_ok=True)
    created = []
    cfg = config_path(repo)
    if not cfg.exists():
        c = Config(test_command=detect_test_command(repo))
        cfg.write_text(yaml.safe_dump(c.model_dump(), sort_keys=False), encoding="utf-8")
        created.append(str(cfg.relative_to(repo)))
    gi = evo / ".gitignore"
    if not gi.exists():
        gi.write_text("run/\n", encoding="utf-8")
    rules = evo / "rules.md"
    if not rules.exists():
        rules.write_text(RULES_TEMPLATE, encoding="utf-8")
        created.append(str(rules.relative_to(repo)))
    return {"created": created, "test_command": load_config(repo).test_command}


# ---------------------------------------------------------------- git helpers
def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()
