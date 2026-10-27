"""Elephant: the long-memory planner. It writes the ECP. It NEVER writes code.

It remembers everything: the repo's files, the rules, and past ECPs (what was
tried and how it ended). From a change request it drafts an ECP; when Goldfish
reports problems it revises the same ECP.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .config import Config
from .ecp import ECP, next_id
from .llm import LLM, extract_json

SYSTEM = """You are Elephant, the planner. You write an Evolution Candidate Profile (ECP) for a requested change.
You never write code. You only plan.

Reply with ONLY a JSON object with these fields:
{
 "title": str,
 "change_type": "bug" | "feature" | "enhancement" | "refactor",
 "intent": str (what must be true after the change, and why),
 "allowed_paths": [globs of the ONLY files that may be edited, as narrow as possible],
 "forbidden_paths": [globs that must never be touched],
 "constraints": [rules the change must obey, taken from RULES.md and common sense],
 "success_criteria": [measurable statements],
 "verification": [{"description": str, "command": str (shell command that exits 0 on success, or "" for the main test command)}],
 "abort_conditions": [when to stop and roll back],
 "rollback": str,
 "risk": "low" | "high",
 "body": str (markdown: root cause or reasoning, and the planned approach in plain steps; mention past ECPs if relevant)
}
Plan small. A bug fix needs a test that fails before the fix. Do not allow paths you do not need."""


SKIP_DIRS = {".git", ".evolution", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".next", "target", "vendor", ".idea", ".vscode", ".pytest_cache"}
SKIP_SUFFIX = (".lock", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".min.js", ".map", ".svg", ".woff", ".woff2")


def repo_overview(repo: Path, max_bytes: int = 150_000) -> str:
    out, used = [], 0
    for f in sorted(Path(repo).rglob("*")):
        rel = f.relative_to(repo).as_posix()
        if not f.is_file() or SKIP_DIRS & set(rel.split("/")[:-1]) or rel.endswith(SKIP_SUFFIX) or f.stat().st_size > 60_000:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if used + len(text) > max_bytes:
            out.append(f"--- {rel} (skipped, size budget) ---")
            continue
        used += len(text)
        out.append(f"--- {rel} ---\n{text}")
    return "\n\n".join(out)


def history(repo: Path) -> str:
    p = Path(repo) / ".evolution" / "history.jsonl"
    if not p.exists():
        return "(no past ECPs)"
    return "\n".join(f"- {json.loads(l)['id']}: {json.loads(l)['title']} -> {json.loads(l)['outcome']}: {json.loads(l)['summary']}" for l in p.read_text().splitlines() if l.strip())


def _build(data: dict, repo: Path, base_commit: str, keep: ECP | None) -> ECP:
    data = {k: v for k, v in data.items() if k in ECP.model_fields}
    data["id"] = keep.id if keep else next_id(repo)
    data["base_commit"] = base_commit
    data["status"] = "proposed"
    data["plan_hash"] = ""
    return ECP(**data)


def _ask(llm, cfg, role, user, repo, base_commit, keep):
    """Ask the model for an ECP; if the answer is malformed, tell it what was wrong and ask once more."""
    try:
        return _build(extract_json(llm.complete(role, SYSTEM, user, cfg.models.elephant)), repo, base_commit, keep)
    except (ValueError, ValidationError, TypeError) as e:
        retry = user + f"\n\nYour previous reply could not be used ({e}). Reply again with ONLY the JSON object, with every field in the right type."
        return _build(extract_json(llm.complete(role, SYSTEM, retry, cfg.models.elephant)), repo, base_commit, keep)


def draft(llm: LLM, cfg: Config, repo: Path, request: str, base_commit: str) -> ECP:
    rules = (Path(repo) / ".evolution" / "rules.md").read_text(encoding="utf-8")
    user = f"CHANGE REQUEST:\n{request}\n\nRULES.md:\n{rules}\n\nPAST ECPs:\n{history(repo)}\n\nREPO FILES:\n{repo_overview(repo, cfg.context_bytes)}"
    return _ask(llm, cfg, "elephant:draft", user, repo, base_commit, None)


def revise(llm: LLM, cfg: Config, repo: Path, ecp: ECP, findings: list, request: str) -> ECP:
    rules = (Path(repo) / ".evolution" / "rules.md").read_text(encoding="utf-8")
    fl = "\n".join(f"- [{f.severity}] ({f.reviewer}) {f.location}: {f.claim} Suggested: {f.suggested_fix}" for f in findings)
    user = (
        f"CHANGE REQUEST:\n{request}\n\nRULES.md:\n{rules}\n\nYOUR CURRENT ECP:\n{ecp.to_markdown()}\n\n"
        f"INDEPENDENT REVIEWERS FOUND THESE PROBLEMS:\n{fl}\n\nREPO FILES:\n{repo_overview(repo, cfg.context_bytes)}\n\n"
        "Produce the revised ECP (full JSON). Fix what is valid. If you disagree with a finding, explain why in the body."
    )
    return _ask(llm, cfg, "elephant:revise", user, repo, ecp.base_commit, ecp)
