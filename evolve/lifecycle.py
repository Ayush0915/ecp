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


def approved_hash(repo: Path, ecp_id: str) -> str:
    """The plan hash written into the evidence log at the moment of approval (the record, not the object, is the truth)."""
    for e in reversed(Evidence(repo, ecp_id).entries()):
        if e["kind"] == "status" and e["data"].get("to") == "approved":
            return e["data"].get("plan_hash", "")
    return ""


def assert_lock(ecp: ECP, repo: Path) -> None:
    """The plan in memory AND the ECP file on disk must both still match the hash recorded at approval."""
    rec = approved_hash(repo, ecp.id)
    if not rec or ecp.plan_hash != rec:
        raise BadTransition(f"{ecp.id}: no matching approval in the evidence log; refusing to continue")
    if ecp.compute_hash() != rec:
        raise BadTransition(f"{ecp.id}: the locked plan was changed after approval; refusing to continue")
    path = Path(repo) / ".evolution" / "ecps" / f"{ecp.id}.md"
    try:
        disk = ECP.load(path)
    except Exception as e:  # noqa: BLE001
        raise BadTransition(f"{ecp.id}: the ECP file can no longer be read ({e}); refusing to continue") from None
    if disk.compute_hash() != rec:
        raise BadTransition(f"{ecp.id}: the ECP file on disk was changed after approval; refusing to continue")


def transition(ecp: ECP, new: str, repo: Path, note: str = "") -> None:
    if new not in ALLOWED[ecp.status]:
        raise BadTransition(f"{ecp.id}: cannot go from '{ecp.status}' to '{new}'")
    if new == "approved":
        if ecp.problems():
            raise BadTransition(f"{ecp.id}: cannot approve an incomplete ECP: {ecp.problems()}")
        ecp.lock()
    if new in ("executing", "verified"):
        assert_lock(ecp, repo)
    old, ecp.status = ecp.status, new
    ecp.save(repo)
    Evidence(repo, ecp.id).add("status", {"from": old, "to": new, "note": note, "plan_hash": ecp.plan_hash})
