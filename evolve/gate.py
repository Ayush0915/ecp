"""The risk gate: who has to say yes?

  low risk  -> automatic (if no blocking findings are left)
  high risk -> a human must approve
A plan is forced to HIGH when it touches a path the repo marked as protected.
Never decided by an AI: plain rules only.
"""
from __future__ import annotations

from typing import Callable

from .config import Config
from .ecp import ECP
from .scope import matches


def effective_risk(ecp: ECP, cfg: Config) -> str:
    if ecp.risk == "high":
        return "high"
    if cfg.protected_paths and any(matches(p, cfg.protected_paths) or p in cfg.protected_paths for p in ecp.allowed_paths):
        return "high"
    if cfg.protected_paths:
        # a broad allowed glob could cover a protected file
        for allowed in ecp.allowed_paths:
            for prot in cfg.protected_paths:
                if matches(prot, [allowed]):
                    return "high"
    return "low"


def decide(ecp: ECP, cfg: Config, what: str, ask: Callable[[str], bool]) -> tuple[bool, str]:
    """Return (go_ahead, how). `ask` is called only for high-risk changes."""
    risk = effective_risk(ecp, cfg)
    if risk == "low":
        return True, "auto (low risk)"
    ok = ask(f"{ecp.id} is HIGH risk. {what}")
    return ok, "human approved" if ok else "human declined"
