"""The final step: keep the change (merge) or throw it away (roll back), and remember why."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import git
from .ecp import ECP

RECORD_PATHS = [".evolution/ecps", ".evolution/evidence", ".evolution/history.jsonl", ".evolution/findings", ".evolution/baseline.json", ".evolution/discovery.jsonl", ".evolution/.gitignore"]
# config.yaml / rules.md / governance.yaml live in the governance store outside the repo (store.py); the evidence log keeps
# their fingerprints and the profile used for each run.
TRAILER = "Evidence-Head"


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
    """Commit the records. The commit message carries the head of the evidence chain (a git trailer), so rewriting the
    whole chain afterwards, even consistently, no longer matches what git recorded."""
    from .ecp import Evidence
    ident = _identity_args(repo)
    present = record_paths_present(repo)
    entries = Evidence(repo, ecp.id).entries()
    head = entries[-1]["hash"] if entries else "genesis"
    git(repo, "add", "-f", "--", *present, check=False)
    msg = f"evolve: record {ecp.id} ({outcome})\n\n{TRAILER}: {ecp.id} {head}"
    git(repo, *ident, "commit", "-q", "-m", msg, "--", *present, check=False)


def anchor_status(repo: Path, ecp_id: str) -> dict:
    """Compare the evidence chain's head with the one git recorded. state: ok | mismatch | none."""
    from .ecp import Evidence
    out = git(repo, "log", "-n", "1", "--format=%H%n%B", f"--grep=^{TRAILER}: {ecp_id} ", check=False)
    recorded = ""
    for line in out.splitlines():
        if line.startswith(f"{TRAILER}: {ecp_id} "):
            recorded = line.split()[-1]
    if not recorded:
        return {"state": "none", "note": "no record commit for this ECP yet"}
    entries = Evidence(repo, ecp_id).entries()
    head = entries[-1]["hash"] if entries else "genesis"
    if head == recorded:
        return {"state": "ok", "note": f"evidence head {head} matches the record commit {out.splitlines()[0][:10]}"}
    return {"state": "mismatch", "note": f"evidence head {head} differs from {recorded} recorded in git: the log was changed after it was committed"}
