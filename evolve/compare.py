"""With and without governance, side by side.

Runs the SAME request twice on the SAME repo:
  1. PLAIN  : the coding agent gets the request and nothing else. No ECP, no review, no scope limit, no record.
              Its work is measured (files touched, do tests pass, was a test added) and then thrown away.
  2. GOVERNED: the normal flow (Elephant -> Goldfish -> Pi under the guard -> verify -> decide).
The result is saved in .evolution/compare/latest.json and shown on the dashboard.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from . import engine
from .config import Config, git
from .decide import _identity_args, record_paths_present
from .llm import LLM
from .pi_runner import PASS_ENV, pi_args
from .scope import normalize
from .verify import is_test_file, run_cmd


def _plain_prompt(request: str) -> str:
    return f"{request}\n\nMake the change in this repository. Run nothing destructive. When finished, stop."


def run_plain_scripted(repo: Path, actions: list[dict]) -> str:
    """Apply the actions with NO scope check (this is what 'no governance' means)."""
    err = ""
    for a in actions:
        target = Path(repo) / normalize(a["path"]) if normalize(a["path"]) else None
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if a["tool"] == "write":
            target.write_text(a["content"], encoding="utf-8")
        elif a["tool"] == "edit":
            text = target.read_text(encoding="utf-8")
            if a["old"] in text:
                target.write_text(text.replace(a["old"], a["new"], 1), encoding="utf-8")
            else:
                err = f"edit failed in {a['path']}"
    return err


def run_plain_pi(repo: Path, cfg: Config, request: str) -> str:
    """The real Pi agent with default tools and no guard extension, in the same sandbox as the governed run."""
    prompt = _plain_prompt(request)
    args = ["-p", "--mode", "json", "--no-session", "--no-context-files", "--tools", "read,grep,find,ls,edit,write,bash"]
    if cfg.pi.model:
        args += ["--model", cfg.pi.model]
    args.append(prompt)
    if cfg.pi.sandbox == "podman":
        exe = shutil.which("podman")
        if not exe:
            return "podman is not installed."
        cmd = [exe, "run", "--rm", "--userns=keep-id", "--security-opt", "no-new-privileges", "--cap-drop=ALL", "-v", f"{Path(repo).resolve()}:/workspace:Z"]
        for k in PASS_ENV:
            if os.environ.get(k):
                cmd += ["-e", k]
        if os.environ.get("AZURE_OPENAI_ENDPOINT") and not os.environ.get("AZURE_OPENAI_BASE_URL"):
            cmd += ["-e", f"AZURE_OPENAI_BASE_URL={os.environ['AZURE_OPENAI_ENDPOINT']}"]
        cmd += [cfg.pi.image, *args]
    else:
        exe = shutil.which("pi")
        if not exe:
            return "Pi is not installed."
        cmd = [exe, *args]
    try:
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=cfg.pi.timeout_s)
        return "" if r.returncode == 0 else (r.stderr or r.stdout)[-500:]
    except subprocess.TimeoutExpired:
        return f"timed out after {cfg.pi.timeout_s}s"


def _changed_everything(repo: Path) -> list[str]:
    """Every file that differs from HEAD (edited, deleted or new), including the .evolution folder."""
    changed = git(repo, "diff", "--name-only", "HEAD").splitlines() + git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({f.strip() for f in changed if f.strip() and not f.startswith((".evolution/run/", ".evolution/reports/"))})


def _commit_pending_records(repo: Path) -> None:
    present = record_paths_present(repo)
    if present:
        git(repo, "add", "-f", "--", *present, check=False)
        git(repo, *_identity_args(repo), "commit", "-q", "-m", "evolve: save records before comparison", "--", *present, check=False)


def plain_run(repo: Path, cfg: Config, request: str, actions: list[dict] | None, base_branch: str) -> dict:
    branch = f"evolve/plain-{int(time.time())}"
    git(repo, "checkout", "-q", "-b", branch)
    t0 = time.time()
    try:
        err = run_plain_pi(repo, cfg, request) if cfg.pi.backend == "pi" else run_plain_scripted(repo, actions or [])
        changed = _changed_everything(repo)
        ok, out = run_cmd(repo, cfg.test_command) if cfg.test_command else (False, "no test command")
        result = {"changed_files": changed, "tests_ok": ok, "test_tail": out[-300:], "test_added": any(is_test_file(f) for f in changed),
                  "error": err, "seconds": round(time.time() - t0, 1), "had_plan": False, "had_review": False, "had_scope_limit": False, "had_record": False}
    finally:
        git(repo, "reset", "-q", "--hard", "HEAD", check=False)
        git(repo, "clean", "-fdq", check=False)
        git(repo, "checkout", "-q", base_branch)
        git(repo, "branch", "-q", "-D", branch, check=False)
    return result


def compare(repo: Path, request: str, llm: LLM, cfg: Config, plain_actions: list[dict] | None, pi_actions: list[dict] | None,
            ask: Callable[[str], bool] = lambda q: True, log: Callable[[str], None] = lambda s: None) -> dict:
    repo = Path(repo)
    if git(repo, "status", "--porcelain", "--", ".", ":(exclude).evolution"):
        raise RuntimeError("The repo has uncommitted changes. Commit or stash them first.")
    _commit_pending_records(repo)
    base_branch = cfg.base_branch or git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    log("PLAIN run: the agent gets only the request (no plan, no review, no limits)...")
    plain = plain_run(repo, cfg, request, plain_actions, base_branch)
    log(f"  plain agent touched {len(plain['changed_files'])} file(s): {', '.join(plain['changed_files']) or 'none'}; tests {'pass' if plain['tests_ok'] else 'FAIL'}")
    log("GOVERNED run: the normal ECP flow...")
    res = engine.run(repo, request, llm, cfg, pi_actions, ask, log)
    allowed = res.ecp.allowed_paths if res.ecp else []
    from .scope import matches
    collateral = [f for f in plain["changed_files"] if not matches(f, allowed)]
    governed = {"ecp": res.ecp.id if res.ecp else "", "outcome": res.outcome, "reasons": res.reasons, "rounds": res.rounds,
                "allowed_paths": allowed, "blocked_attempts": res.blocked_edits, "had_plan": True, "had_review": True, "had_scope_limit": True, "had_record": True}
    out = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "request": request, "plain": plain, "governed": governed, "plain_outside_governed_scope": collateral}
    d = repo / ".evolution" / "compare"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def format_comparison(c: dict) -> str:
    p, g = c["plain"], c["governed"]
    yes = lambda b: "yes" if b else "NO"
    rows = [
        ("Written plan before changes", yes(p["had_plan"]), yes(g["had_plan"])),
        ("Independent review of the plan", yes(p["had_review"]), f"yes ({g['rounds']} round(s))"),
        ("Edits limited to declared files", yes(p["had_scope_limit"]), f"yes ({len(g['blocked_attempts'])} blocked)"),
        ("Files changed", str(len(p["changed_files"])), "see ECP (only allowed files)"),
        ("Files changed outside the governed scope", str(len(c["plain_outside_governed_scope"])), "0 (blocked or rolled back)"),
        ("Test added that fails on old code", "not checked", "checked"),
        ("Permanent record of why/what", yes(p["had_record"]), yes(g["had_record"])),
        ("Final result", "tests " + ("pass" if p["tests_ok"] else "FAIL") + ", unreviewed", g["outcome"].upper().replace("_", " ")),
    ]
    w = max(len(r[0]) for r in rows)
    lines = [f"{'':{w}}  {'PLAIN AGENT':<26} GOVERNED (ECP)"] + [f"{a:{w}}  {b:<26} {c_}" for a, b, c_ in rows]
    if c["plain_outside_governed_scope"]:
        lines.append("\nThe plain agent also changed: " + ", ".join(c["plain_outside_governed_scope"]))
    return "\n".join(lines)
