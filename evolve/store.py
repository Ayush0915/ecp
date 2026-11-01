"""The governance store: where a repository's RULES live. Deliberately OUTSIDE the repository.

    $EVOLVE_HOME/repos/<name>-<hash>/      (EVOLVE_HOME defaults to ~/.evolve)
        config.yaml      test command, sandbox, model deployments
        rules.md         rules every plan must follow
        governance.yaml  autonomy level, risk thresholds, required reviewers
        repo.txt         which working copy this store belongs to

Why outside: the repository being governed must not be able to set its own governance. Anything inside the repository
can be changed by whoever controls the repository (a pull, a malicious clone, or the agent itself). Records of what
happened (ECPs, evidence, history) still live in the repository's .evolution/ folder, where they travel with the code.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path


def evolve_home() -> Path:
    return Path(os.environ.get("EVOLVE_HOME") or Path.home() / ".evolve").expanduser()


def repo_key(repo: Path) -> str:
    """Stable folder name for one working copy: readable name + hash of its absolute path."""
    r = Path(repo).resolve()
    ident = str(r).lower() if os.name == "nt" else str(r)
    name = re.sub(r"[^A-Za-z0-9._-]", "-", r.name)[:40] or "repo"
    return f"{name}-{hashlib.sha256(ident.encode()).hexdigest()[:10]}"


def store_dir(repo: Path, create: bool = True) -> Path:
    d = evolve_home() / "repos" / repo_key(repo)
    if create and not d.exists():
        d.mkdir(parents=True, exist_ok=True)
        (d / "repo.txt").write_text(str(Path(repo).resolve()) + "\n", encoding="utf-8")
    return d
