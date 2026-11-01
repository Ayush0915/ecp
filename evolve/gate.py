"""The risk gate: who has to say yes? Never decided by an AI: plain rules only.

Risk is computed twice, from facts:
  assess()       on the PLAN, before Pi builds (scope, file kinds, change type, size)
  assess_diff()  on the real DIFF, before the change is kept (what was actually touched, and how much)
The planner (Elephant) may SUGGEST high risk; that raises it, but nothing an AI says can lower it.

Who decides (decide()):
  low risk, autonomy level 2 or 3   -> automatic
  high risk                         -> a human
  autonomy level 1                  -> a human, every time
  a gate listed in human_gates      -> a human, every time
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Callable

from .config import Config
from .ecp import ECP
from .governance import Governance
from .scope import matches


def _overlap(items: list[str], patterns: list[str]) -> list[str]:
    """Items (paths or globs) that a pattern covers, or that cover a pattern (a broad glob can include a sensitive file)."""
    return sorted({i for i in items if matches(i, patterns) or any(matches(p, [i]) for p in patterns)})


def assess(ecp: ECP, cfg: Config, gov: Governance | None = None, repo: Path | None = None) -> tuple[str, list[str]]:
    """Risk of the PLAN, with the reasons. 'high' if any rule fires."""
    gov = gov or Governance()
    reasons: list[str] = []
    if ecp.risk_suggested == "high":
        reasons.append("the planner suggested high risk")
    elif ecp.risk == "high":
        reasons.append("the plan is marked high risk")
    files: list[str] = []
    if repo is not None:
        from .plancheck import files_in_scope
        files = files_in_scope(repo, ecp)
    items = list(dict.fromkeys([*ecp.allowed_paths, *ecp.new_files, *files]))
    if cfg.protected_paths:
        hit = _overlap(items, cfg.protected_paths)
        if hit:
            reasons.append(f"touches protected paths: {', '.join(hit[:4])}")
    hit = _overlap(items, gov.high_risk_paths)
    if hit:
        reasons.append(f"touches sensitive files (dependencies, CI, auth or secrets): {', '.join(hit[:4])}")
    if ecp.change_type == "feature":
        reasons.append("new features are always high risk")
    if ecp.change_type not in gov.allowed_change_types:
        reasons.append(f"change type '{ecp.change_type}' is not in allowed_change_types")
    n = len(files) if repo is not None else len(ecp.allowed_paths) + len(ecp.new_files)
    if n > gov.high_risk_files:
        reasons.append(f"{n} files in scope (more than {gov.high_risk_files})")
    return ("high" if reasons else "low"), reasons


def diff_lines(diff: str) -> int:
    return sum(1 for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---")))


def assess_diff(changed: list[str], diff: str, cfg: Config, gov: Governance | None = None) -> tuple[str, list[str], list[str]]:
    """Risk of what was ACTUALLY built. Returns (risk, reasons, hard_failures). Hard failures roll the change back."""
    gov = gov or Governance()
    reasons, hard = [], []
    n, lines = len(changed), diff_lines(diff)
    if n > gov.high_risk_files:
        reasons.append(f"the diff touches {n} files (more than {gov.high_risk_files})")
    if lines > gov.high_risk_diff_lines:
        reasons.append(f"the diff changes {lines} lines (more than {gov.high_risk_diff_lines})")
    hit = _overlap(changed, gov.high_risk_paths + cfg.protected_paths)
    if hit:
        reasons.append(f"the diff touches sensitive or protected files: {', '.join(hit[:4])}")
    if lines > gov.max_diff_lines:
        hard.append(f"the diff changes {lines} lines, above the limit of {gov.max_diff_lines} (governance max_diff_lines)")
    return ("high" if reasons else "low"), reasons, hard


def effective_risk(ecp: ECP, cfg: Config) -> str:
    """Kept for callers that only need the word."""
    return assess(ecp, cfg)[0]


def _ask(ask: Callable, question: str, ctx: dict | None):
    """Call `ask` with the decision context (ECP id, plan hash...) if it accepts it; plain one-argument functions still work."""
    try:
        params = inspect.signature(ask).parameters.values()
        takes_ctx = sum(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) for p in params) >= 2 or any(p.kind == p.VAR_POSITIONAL for p in params)
    except (TypeError, ValueError):
        takes_ctx = False
    return ask(question, ctx) if takes_ctx else ask(question)


def decide(ecp: ECP, cfg: Config, what: str, ask: Callable, gov: Governance | None = None, stage: str = "plan",
           risk: str | None = None, reasons: list[str] | None = None, ctx: dict | None = None) -> tuple[bool, str]:
    """Return (go_ahead, how). `ask` is called only when a human must decide. It may return yes/no or (yes/no, how),
    so the record says truthfully who decided: a person in the terminal, a person in the dashboard, or the --yes flag."""
    gov = gov or Governance()
    if risk is None:
        risk, reasons = assess(ecp, cfg, gov)
    reasons = reasons or []
    why = []
    if gov.autonomy_level <= 1:
        why.append(f"autonomy level {gov.autonomy_level}: a human decides every {stage}")
    if stage in gov.human_gates:
        why.append(f"governance requires a human at the {stage} gate")
    if risk != "high" and not why:
        return True, "auto (low risk)"
    if risk == "high":
        question = f"{ecp.id} is HIGH risk ({'; '.join(reasons) or 'no reason recorded'}). {what}"
    else:
        question = f"{ecp.id} needs a human decision ({'; '.join(why)}). {what}"
    answer = _ask(ask, question, ctx)
    if isinstance(answer, tuple):
        return bool(answer[0]), str(answer[1])
    return bool(answer), "human approved" if answer else "human declined"
