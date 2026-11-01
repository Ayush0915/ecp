"""Deterministic checks on a plan's scope, using the repository and the governance profile. No AI.

Each problem is a plain sentence; the engine turns them into blocking findings, so Elephant must fix the plan.
"""
from __future__ import annotations

from pathlib import Path

from .config import git
from .ecp import ECP
from .governance import Governance
from .scope import matches
from .verify import is_test_file


def _clean(p: str) -> str:
    p = p.strip().replace("\\", "/")
    return p[2:] if p.startswith("./") else p


def tracked_files(repo: Path) -> list[str]:
    return [f for f in git(repo, "ls-files", check=False).splitlines() if f and not f.startswith(".evolution/")]


def files_in_scope(repo: Path, ecp: ECP, tracked: list[str] | None = None) -> list[str]:
    """Existing files the allowed globs cover, plus the files the plan says it will create."""
    tracked = tracked_files(repo) if tracked is None else tracked
    allowed = [_clean(a) for a in ecp.allowed_paths]
    return sorted({f for f in tracked if matches(f, allowed)} | {_clean(n) for n in ecp.new_files})


def plan_problems(ecp: ECP, repo: Path, gov: Governance) -> list[str]:
    out: list[str] = []
    tracked = tracked_files(repo)
    new = [_clean(n) for n in ecp.new_files]
    allowed = [_clean(a) for a in ecp.allowed_paths]
    for raw, g in zip(ecp.allowed_paths, allowed):
        first = g.split("/")[0]
        if first in ("*", "**", ""):
            out.append(f"allowed path '{raw}' is too broad: it starts at the repository root with a wildcard; name the folder or the files")
        elif not any(matches(f, [g]) for f in tracked) and not any(matches(n, [g]) for n in new):
            out.append(f"allowed path '{raw}' matches no file in the repository; if the change creates that file, list it in new_files")
    for raw, n in zip(ecp.new_files, new):
        if not matches(n, allowed):
            out.append(f"new file '{raw}' is not covered by allowed_paths, so it could not be created")
        if n in tracked:
            out.append(f"new file '{raw}' already exists; list it only in allowed_paths")
    scope = files_in_scope(repo, ecp, tracked)
    if len(scope) > gov.max_files:
        out.append(f"{len(scope)} files are in scope, more than the limit of {gov.max_files} (governance max_files); make the change smaller")
    if ecp.change_type == "bug" and not any(is_test_file(f) for f in scope + allowed):
        out.append("a bug fix must be allowed to add or change a test, but no test file is in scope")
    return out
