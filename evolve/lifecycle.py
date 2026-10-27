"""The ECP's life, in order. Skipping a step is not allowed.

  proposed -> reviewed -> approved -> executing -> verified -> decided
              ^   |
              +---+  (a plan can go back to 'proposed' when it is revised)
"""
from __future__ import annotations

from pathlib import Path

from .ecp import ECP, Evidence

ORDER = ["proposed", "reviewed", "approved", "executing", "verified", "decided"]
ALLOWED = {
    "proposed": {"reviewed"},
    "reviewed": {"proposed", "approved", "decided"},  # decided = rejected before any code ran
    "approved": {"executing", "decided"},
    "executing": {"verified", "decided"},  # decided = aborted / failed
    "verified": {"decided"},
    "decided": set(),
}


class BadTransition(Exception):
    pass


def transition(ecp: ECP, new: str, repo: Path, note: str = "") -> None:
    if new not in ALLOWED[ecp.status]:
        raise BadTransition(f"{ecp.id}: cannot go from '{ecp.status}' to '{new}'")
    if new == "approved":
        if ecp.problems():
            raise BadTransition(f"{ecp.id}: cannot approve an incomplete ECP: {ecp.problems()}")
        ecp.lock()
    if new in ("executing", "verified") and not ecp.lock_intact():
        raise BadTransition(f"{ecp.id}: the locked plan was changed after approval; refusing to continue")
    old, ecp.status = ecp.status, new
    ecp.save(repo)
    Evidence(repo, ecp.id).add("status", {"from": old, "to": new, "note": note, "plan_hash": ecp.plan_hash})
