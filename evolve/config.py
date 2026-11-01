"""Per-repo settings. This is what makes the tool work for ANY repo: nothing is hard-coded to one project.

The settings (config.yaml), the rules (rules.md) and the governance profile (governance.yaml) live in the governance
store OUTSIDE the repository (see store.py), so the repository being governed cannot change its own governance.
The repository's .evolution/ folder holds only records (ECPs, evidence, history, findings)."""
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
    allowed_check_prefixes: list[str] = Field(default_factory=list)  # extra ECP check commands to allow, e.g. ["make test"] (no shell is used)


def config_path(repo: Path) -> Path:
    from .store import store_dir
    return store_dir(repo, create=False) / "config.yaml"


def rules_path(repo: Path) -> Path:
    from .store import store_dir
    return store_dir(repo, create=False) / "rules.md"


def governance_path(repo: Path) -> Path:
    from .store import store_dir
    return store_dir(repo, create=False) / "governance.yaml"


def is_initialised(repo: Path) -> bool:
    return config_path(repo).exists()


def read_rules(repo: Path) -> str:
    p = rules_path(repo)
    return p.read_text(encoding="utf-8") if p.exists() else "(no rules)"


def load_config(repo: Path) -> Config:
    p = config_path(repo)
    if not p.exists():
        raise FileNotFoundError(f"{p} not found. Run `evolve init --repo {repo}` first.")
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
    """Create the records folder in the repo and the settings in the governance store. Safe to re-run (never overwrites)."""
    from .governance import write_default
    from .store import store_dir
    repo = Path(repo)
    if not (repo / ".git").exists():
        raise RuntimeError("This folder is not a git repo. Evolution needs git to roll back safely (run `git init`).")
    evo = repo / ".evolution"
    for sub in ("ecps", "evidence"):
        (evo / sub).mkdir(parents=True, exist_ok=True)
    gi = evo / ".gitignore"
    if not gi.exists():
        gi.write_text("run/\nuntrusted/\n", encoding="utf-8")
    store = store_dir(repo)
    created = []
    cfg = config_path(repo)
    if not cfg.exists():
        c = Config(test_command=detect_test_command(repo))
        cfg.write_text(yaml.safe_dump(c.model_dump(), sort_keys=False), encoding="utf-8")
        created.append(str(cfg))
    rules = rules_path(repo)
    if not rules.exists():
        rules.write_text(RULES_TEMPLATE, encoding="utf-8")
        created.append(str(rules))
    gov = governance_path(repo)
    if not gov.exists():
        write_default(gov)
        created.append(str(gov))
    return {"created": created, "test_command": load_config(repo).test_command, "store": str(store)}


GOVERNANCE_FILES = ("config.yaml", "rules.md")


def quarantine_repo_governance(repo: Path, why: str = "it came with the repository") -> list[str]:
    """The repository being governed must not set its own rules or test command (a test_command is run on this computer).
    Files it ships in .evolution/ (config.yaml, rules.md) are moved to .evolution/untrusted/ for a person to read;
    init_repo then writes fresh defaults. Returns the files that were moved."""
    evo = Path(repo) / ".evolution"
    moved = []
    for name in GOVERNANCE_FILES:
        f = evo / name
        if f.is_file():
            dest = evo / "untrusted" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.unlink(missing_ok=True)
            f.replace(dest)
            moved.append(f".evolution/{name}")
    if moved:
        (evo / "untrusted" / "README.txt").write_text(
            f"These files were set aside because {why}: {', '.join(moved)}.\n"
            "A repository must not decide its own governance (rules, test command, sandbox). Read them; copy anything you trust\n"
            "into the real .evolution/ files yourself.\n", encoding="utf-8")
    return moved


# ---------------------------------------------------------------- git helpers
def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()
