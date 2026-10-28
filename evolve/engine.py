"""The control plane: runs the whole story in order. Plain code, no AI of its own.

  draft -> (review -> revise)* -> approve -> build -> verify -> post-review -> decide
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import decide as decide_mod
from . import elephant, gate, goldfish, lifecycle, pi_runner, verify as verify_mod
from .config import Config, git
from .ecp import ECP, Evidence, save_version
from .goldfish.runner import Finding, blocking
from .llm import LLM
from .live import emit, save_as


class RunResult:
    def __init__(self):
        self.ecp: ECP | None = None
        self.outcome = ""  # accepted | rolled_back | rejected
        self.reasons: list[str] = []
        self.rounds = 0
        self.before_findings: list[list[Finding]] = []
        self.after_findings: list[Finding] = []
        self.blocked_edits: list[dict] = []
        self.verify_summary = ""


def _ecp_view(ecp: ECP) -> dict:
    """The plan as the dashboard draws it."""
    return {"id": ecp.id, "title": ecp.title, "change_type": ecp.change_type, "intent": ecp.intent, "allowed_paths": ecp.allowed_paths,
            "forbidden_paths": ecp.forbidden_paths, "constraints": ecp.constraints, "success_criteria": ecp.success_criteria,
            "verification": [v.model_dump() if hasattr(v, "model_dump") else v for v in ecp.verification], "abort_conditions": ecp.abort_conditions,
            "rollback": ecp.rollback, "risk": ecp.risk, "body": ecp.body}


def _dirty(repo: Path) -> str:
    return git(repo, "status", "--porcelain", "--", ".", ":(exclude).evolution")


def run(
    repo: Path,
    request: str,
    llm: LLM,
    cfg: Config,
    pi_actions: list[dict] | None = None,
    ask: Callable[[str], bool] = lambda q: False,
    log: Callable[[str], None] = lambda s: None,
    finding: dict | None = None,
) -> RunResult:
    """Run one evolution. `finding` (from discovery) is optional: it links the ECP back to the evidence that started it."""
    res = _run(repo, request, llm, cfg, pi_actions, ask, log, finding)
    if res.ecp:
        save_as(repo, res.ecp.id)  # keep the live feed so this run can be replayed later
    if finding and res.ecp:
        from .discover import mark_finding
        mark_finding(Path(repo), finding["id"], res.outcome, res.ecp.id)
    return res


def _run(repo, request, llm, cfg, pi_actions, ask, log, finding):
    repo = Path(repo)
    res = RunResult()
    if _dirty(repo):
        raise RuntimeError("The repo has uncommitted changes. Commit or stash them first so a rollback is safe.")
    base_commit = git(repo, "rev-parse", "HEAD")
    base_branch = cfg.base_branch or git(repo, "rev-parse", "--abbrev-ref", "HEAD")

    # ---- 1. PLAN (Elephant) -------------------------------------------------
    log("Elephant is drafting the ECP...")
    emit(repo, "elephant_start", task="draft", model=cfg.models.resolve("elephant"))
    ecp = elephant.draft(llm, cfg, repo, request, base_commit)
    res.ecp = ecp
    ecp.save(repo)
    ev = Evidence(repo, ecp.id)
    ev.add("request", {"text": request})
    if finding:
        ev.add("finding", {"id": finding["id"], "title": finding["title"], "metric": finding["metric"], "evidence": finding["evidence"],
                           "evidence_why": finding["evidence_why"], "file": finding["file"], "line": finding["line"]})
    version = 1
    save_version(repo, ecp, version)
    ev.add("draft", {"title": ecp.title, "change_type": ecp.change_type, "allowed_paths": ecp.allowed_paths, "version": version})
    log(f"  {ecp.id}: {ecp.title}  [{ecp.change_type}]")
    emit(repo, "elephant_done", task="draft", ecp=_ecp_view(ecp), version=version)

    # ---- 2. REVIEW LOOP (Goldfish before) ----------------------------------
    for round_no in range(1, cfg.max_revise_rounds + 2):
        res.rounds = round_no
        names = [l.name for l in goldfish.roster(ecp.change_type, "before")]
        log(f"Review round {round_no}: Goldfish reviewers {', '.join(names)}")
        emit(repo, "review_start", phase="before", round=round_no, lenses=names)
        findings = goldfish.review(llm, cfg, repo, ecp, "before")
        structural = [Finding(reviewer="completeness", severity="major", location="ECP", claim=p) for p in ecp.problems()]
        findings += structural
        res.before_findings.append(findings)
        ev.add("review_before", {"round": round_no, "findings": [f.model_dump() for f in findings]})
        stoppers = blocking(findings)
        emit(repo, "review_done", phase="before", round=round_no, blocking=len(stoppers), findings=[f.model_dump() for f in findings])
        for f in findings:
            log(f"  [{f.severity}] {f.reviewer}: {f.claim}")
        if not stoppers:
            log("  No blocking findings.")
            break
        if round_no > cfg.max_revise_rounds:
            res.outcome = "rejected"
            res.reasons = [f"Plan still had {len(stoppers)} blocking finding(s) after {cfg.max_revise_rounds} revisions; a human must take over."]
            return _finish(repo, ecp, res, ev, base_branch, log, plan_only=True)
        log("Elephant is revising the plan using the findings...")
        emit(repo, "elephant_start", task="revise", model=cfg.models.resolve("elephant"), problems=len(stoppers))
        ecp = elephant.revise(llm, cfg, repo, ecp, stoppers, request)
        res.ecp = ecp
        ecp.save(repo)
        version += 1
        save_version(repo, ecp, version)
        ev.add("revised", {"round": round_no, "allowed_paths": ecp.allowed_paths, "version": version})
        emit(repo, "elephant_done", task="revise", ecp=_ecp_view(ecp), version=version)

    lifecycle.transition(ecp, "reviewed", repo, f"after {res.rounds} round(s)")

    # ---- 3. APPROVE (risk gate, then LOCK) ----------------------------------
    go, how = gate.decide(ecp, cfg, "Approve this plan for execution?", ask)
    ev.add("gate_plan", {"go": go, "how": how, "risk": gate.effective_risk(ecp, cfg)})
    log(f"Gate (plan): {how}")
    emit(repo, "gate", stage="plan", go=go, how=how, risk=gate.effective_risk(ecp, cfg))
    if not go:
        res.outcome, res.reasons = "rejected", [f"Plan not approved ({how})."]
        return _finish(repo, ecp, res, ev, base_branch, log, plan_only=True)
    lifecycle.transition(ecp, "approved", repo, how)
    log(f"ECP locked (hash {ecp.plan_hash}).")
    emit(repo, "locked", hash=ecp.plan_hash)

    # ---- 4. BUILD (Pi) -------------------------------------------------------
    pi_runner.start_branch(repo, ecp)
    lifecycle.transition(ecp, "executing", repo)
    log(f"Pi is building on branch evolve/{ecp.id} (backend: {cfg.pi.backend})...")
    emit(repo, "pi_start", branch=f"evolve/{ecp.id}", backend=cfg.pi.backend, model=cfg.pi.model, allowed=ecp.allowed_paths)
    pres = pi_runner.execute(repo, ecp, cfg, pi_actions)
    res.blocked_edits = pres.blocked
    for b in pres.blocked:
        log(f"  GUARD BLOCKED {b['tool']} {b['path']}: {b['reason']}")
    log(f"  {len(pres.applied)} edit(s) applied, {len(pres.blocked)} blocked.")
    emit(repo, "pi_done", applied=len(pres.applied), blocked=len(pres.blocked), error=pres.error)
    if pres.error:
        log(f"  Pi problem: {pres.error}")
        ev.add("pi_error", {"error": pres.error})

    # ---- 5. VERIFY (tests + scope, plain programs) --------------------------
    emit(repo, "verify_start")
    v = verify_mod.verify(repo, ecp, cfg)
    res.verify_summary = v.summary()
    ev.add("verify", {"ok": v.ok, "summary": v.summary(), "changed_files": v.changed_files, "test_tail": v.test_output[-800:], "diff": v.diff[:20000], "fail_before": v.fail_before})
    log(f"Verify: {v.summary()}")
    emit(repo, "verify_done", ok=v.ok, summary=v.summary(), changed=v.changed_files, fail_before=v.fail_before, diff=v.diff[:30000], tests_ok=v.test_ok, scope=v.scope_problems)
    lifecycle.transition(ecp, "verified", repo)

    # ---- 6. POST-REVIEW (Goldfish after) -------------------------------------
    names = [l.name for l in goldfish.roster(ecp.change_type, "after")]
    log(f"Goldfish reviewers (after): {', '.join(names)}")
    emit(repo, "review_start", phase="after", round=1, lenses=names)
    after = goldfish.review(llm, cfg, repo, ecp, "after", {"DIFF.patch": v.diff or "(no changes)", "TEST_OUTPUT.txt": v.test_output})
    res.after_findings = after
    ev.add("review_after", {"findings": [f.model_dump() for f in after]})
    emit(repo, "review_done", phase="after", round=1, blocking=len(blocking(after)), findings=[f.model_dump() for f in after])
    for f in after:
        log(f"  [{f.severity}] {f.reviewer}: {f.claim}")

    # ---- 7. DECIDE -------------------------------------------------------------
    reasons = []
    if pres.error:
        reasons.append(f"Pi problem: {pres.error}")
    if not v.test_ok:
        reasons.append("tests failed")
    if v.scope_problems:
        reasons.append("scope violation: " + "; ".join(v.scope_problems))
    for c in v.check_results:
        if not c["ok"]:
            reasons.append(f"check failed: {c['description']}")
    if v.fail_before["status"] == "no_tests":
        reasons.append("no proof: " + v.fail_before["note"])
    elif v.fail_before["status"] == "not_proved":
        reasons.append("no proof: " + v.fail_before["note"])
    if not v.changed_files:
        reasons.append("Pi made no changes")
    for f in blocking(after):
        reasons.append(f"{f.reviewer} [{f.severity}]: {f.claim}")
    if not reasons:
        go, how = gate.decide(ecp, cfg, "Keep this change?", ask)
        ev.add("gate_result", {"go": go, "how": how})
        log(f"Gate (result): {how}")
        emit(repo, "gate", stage="result", go=go, how=how, risk=gate.effective_risk(ecp, cfg))
        if not go:
            reasons.append(f"Result not approved ({how}).")
    res.reasons = reasons
    res.outcome = "rolled_back" if reasons else "accepted"
    return _finish(repo, ecp, res, ev, base_branch, log)


def _finish(repo, ecp, res, ev, base_branch, log, plan_only=False) -> RunResult:
    summary = "; ".join(res.reasons) if res.reasons else "all checks passed"
    if plan_only:
        # never executed: nothing to undo, just close the ECP
        if ecp.status == "reviewed" or ecp.status == "proposed":
            if ecp.status == "proposed":
                lifecycle.transition(ecp, "reviewed", repo, "closing without execution")
            lifecycle.transition(ecp, "decided", repo, res.outcome)
    else:
        if res.outcome == "accepted":
            decide_mod.accept(repo, ecp, base_branch)
            log(f"ACCEPTED: change merged into {base_branch}.")
        else:
            decide_mod.rollback(repo, ecp, base_branch)
            log("ROLLED BACK: " + summary)
        lifecycle.transition(ecp, "decided", repo, res.outcome)
    ev.add("decision", {"outcome": res.outcome, "reasons": res.reasons})
    emit(repo, "decision", outcome=res.outcome, reasons=res.reasons, ecp=ecp.id)
    decide_mod.remember(repo, ecp, res.outcome, summary)
    decide_mod.commit_records(repo, ecp, res.outcome)
    if plan_only:
        log("REJECTED: " + summary)
    return res
