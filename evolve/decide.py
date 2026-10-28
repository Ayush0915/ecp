"""The final step: keep the change (merge) or throw it away (roll back), and remember why."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import git
from .ecp import ECP

RECORD_PATHS = [".evolution/ecps", ".evolution/evidence", ".evolution/history.jsonl", ".evolution/findings", ".evolution/baseline.json", ".evolution/discovery.jsonl", ".evolution/config.yaml", ".evolution/rules.md", ".evolution/.gitignore"]


def record_paths_present(repo: Path) -> list[str]:
    """Record locations that exist and are not empty (git refuses to add an empty folder)."""
    out = []
    for p in RECORD_PATHS:
        full = Path(repo) / p
        if full.is_file() or (full.is_dir() and any(full.rglob("*"))):
            out.append(p)
    return out


def _identity_args(repo: Path) -> list[str]:
    if git(repo, "config", "user.name", check=False):
        return []
    return ["-c", "user.name=Evolve", "-c", "user.email=evolve@localhost"]


def accept(repo: Path, ecp: ECP, base_branch: str) -> None:
    branch = f"evolve/{ecp.id}"
    ident = _identity_args(repo)
    git(repo, *ident, "commit", "-q", "-m", f"{ecp.id}: {ecp.title}\n\n{ecp.intent}")
    git(repo, "checkout", "-q", base_branch)
    git(repo, *ident, "merge", "-q", "--no-ff", "-m", f"Merge {ecp.id}: {ecp.title}", branch)
    git(repo, "branch", "-q", "-d", branch, check=False)


def rollback(repo: Path, ecp: ECP, base_branch: str) -> None:
    """Undo everything the branch did. The .evolution records are kept on purpose (they are the memory)."""
    branch = f"evolve/{ecp.id}"
    git(repo, "restore", "-q", "--staged", "--worktree", "--source=HEAD", "--", ".", ":(exclude).evolution", check=False)
    git(repo, "clean", "-fdq", "-e", ".evolution", "--", ".", check=False)
    git(repo, "checkout", "-q", base_branch)
    git(repo, "branch", "-q", "-D", branch, check=False)


def remember(repo: Path, ecp: ECP, outcome: str, summary: str) -> None:
    """Add one line to history.jsonl so future plans (Elephant) can learn from this one."""
    line = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "id": ecp.id, "title": ecp.title, "change_type": ecp.change_type, "outcome": outcome, "summary": summary}
    with (Path(repo) / ".evolution" / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def commit_records(repo: Path, ecp: ECP, outcome: str) -> None:
    ident = _identity_args(repo)
    present = record_paths_present(repo)
    git(repo, "add", "-f", "--", *present, check=False)
    git(repo, *ident, "commit", "-q", "-m", f"evolve: record {ecp.id} ({outcome})", "--", *present, check=False)
