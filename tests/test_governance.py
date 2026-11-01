"""Phase 2: governance as configuration (docs/CHANGES.md C11-C20)."""
import hashlib
import json
import threading
import time

import pytest
import yaml

from evolve import engine, gate, lifecycle
from evolve.config import Config, config_path, governance_path, git, load_config, read_rules, rules_path
from evolve.ecp import ECP, Evidence
from evolve.governance import Governance, GovernanceError, load_governance, save_governance
from evolve.llm import MockLLM
from evolve.plancheck import plan_problems
from tests.conftest import scenario


def run(repo, sc=None, ask=lambda q: True, llm=None):
    sc = sc or scenario("2_scope_violation_blocked")
    return engine.run(repo, sc["request"], llm or MockLLM(sc), load_config(repo), sc.get("pi_actions"), ask)


def set_gov(repo, **kw):
    return save_governance(repo, kw)


def plan(**kw):
    d = dict(scenario("2_scope_violation_blocked")["elephant"][0]); d.update(kw)
    return ECP(id="ECP-0001", base_commit="x", **d)


# ---------------------------------------------------------------- C11: the store lives outside the repository
def test_settings_rules_and_governance_live_outside_the_repo(demo_repo):
    import os
    store = config_path(demo_repo).parent
    assert str(store).startswith(os.environ["EVOLVE_HOME"])
    assert config_path(demo_repo).exists() and rules_path(demo_repo).exists() and governance_path(demo_repo).exists()
    assert not (demo_repo / ".evolution" / "config.yaml").exists() and not (demo_repo / ".evolution" / "rules.md").exists()
    assert str(demo_repo.resolve()) in (store / "repo.txt").read_text()


def test_a_config_inside_the_repo_is_ignored(demo_repo):
    (demo_repo / ".evolution" / "config.yaml").write_text("test_command: echo PWNED\n")
    (demo_repo / ".evolution" / "rules.md").write_text("- Anything goes.\n")
    assert "PWNED" not in load_config(demo_repo).test_command
    assert "Anything goes" not in read_rules(demo_repo)


def test_records_commit_carries_no_governance_files(demo_repo):
    run(demo_repo)
    files = git(demo_repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert any(f.startswith(".evolution/evidence/") for f in files)
    assert not any(f.endswith(("config.yaml", "rules.md", "governance.yaml")) for f in files)


def test_each_run_records_what_governed_it(demo_repo):
    run(demo_repo)
    snap = next(e for e in Evidence(demo_repo, "ECP-0001").entries() if e["kind"] == "governance")["data"]
    assert snap["governance"]["autonomy_level"] == 2
    assert snap["rules_sha256"] == hashlib.sha256(rules_path(demo_repo).read_bytes()).hexdigest()


# ---------------------------------------------------------------- C12: the profile is strict
def test_governance_defaults_and_typos(demo_repo):
    assert load_governance(demo_repo) == Governance()
    governance_path(demo_repo).write_text("autonomy_levle: 3\n")
    with pytest.raises(GovernanceError, match="autonomy_levle"):
        load_governance(demo_repo)


@pytest.mark.parametrize("bad", [{"autonomy_level": 7}, {"required_lenses": {"bug": ["telepathy"]}}, {"required_lenses": {"chore": []}},
                                 {"human_gates": ["deploy"]}, {"allowed_change_types": ["rewrite"]}])
def test_invalid_profiles_are_refused(demo_repo, bad):
    with pytest.raises(GovernanceError):
        save_governance(demo_repo, bad)


# ---------------------------------------------------------------- C13: autonomy levels
def test_level_0_never_plans(demo_repo):
    set_gov(demo_repo, autonomy_level=0)
    with pytest.raises(GovernanceError, match="discovery only"):
        run(demo_repo)
    assert not list((demo_repo / ".evolution" / "ecps").glob("*.md"))


def test_level_1_asks_a_human_at_both_gates_even_for_low_risk(demo_repo):
    set_gov(demo_repo, autonomy_level=1)
    asked = []
    res = run(demo_repo, ask=lambda q: asked.append(q) or True)
    assert res.outcome == "accepted" and len(asked) == 2 and all("autonomy level 1" in q for q in asked)


def test_level_2_low_risk_runs_without_asking(demo_repo):
    asked = []
    assert run(demo_repo, ask=lambda q: asked.append(q) or True).outcome == "accepted" and not asked


def test_human_gate_on_result_only(demo_repo):
    set_gov(demo_repo, human_gates=["result"])
    asked = []
    res = run(demo_repo, ask=lambda q: asked.append(q) or False)
    assert len(asked) == 1 and "result gate" in asked[0] and res.outcome == "rolled_back"


def test_required_lenses_are_added_to_the_roster(demo_repo):
    set_gov(demo_repo, required_lenses={"bug": ["security", "impact"]})
    sc = scenario("2_scope_violation_blocked")
    llm = MockLLM(sc)
    run(demo_repo, sc, llm=llm)
    roles = {c["role"] for c in llm.calls}
    assert {"goldfish:security", "goldfish:impact"} <= roles


# ---------------------------------------------------------------- C14 / C16: risk from rules; the planner can only raise it
def test_plain_plan_is_low_risk():
    assert gate.assess(plan(), Config()) == ("low", [])


@pytest.mark.parametrize("kw,why", [
    ({"allowed_paths": ["requirements.txt", "tests/test_store.py"]}, "sensitive files"),
    ({"allowed_paths": [".github/workflows/ci.yml"]}, "sensitive files"),
    ({"change_type": "feature"}, "always high risk"),
    ({"risk_suggested": "high"}, "planner suggested"),
    ({"allowed_paths": ["a.py", "b.py", "c.py", "d.py", "tests/test_x.py"]}, "files in scope"),
])
def test_rules_raise_risk(kw, why):
    risk, reasons = gate.assess(plan(**kw), Config())
    assert risk == "high" and any(why in r for r in reasons)


def test_change_type_outside_profile_is_high_risk():
    risk, reasons = gate.assess(plan(change_type="refactor"), Config(), Governance(allowed_change_types=["bug"]))
    assert risk == "high" and "allowed_change_types" in reasons[0]


def test_planner_cannot_lower_the_risk(demo_repo):
    from evolve.elephant import _build
    e = _build({**scenario("2_scope_violation_blocked")["elephant"][0], "risk": "low"}, demo_repo, "x", None)
    assert e.risk == "low" and e.risk_suggested == "low"
    sc = scenario("2_scope_violation_blocked")
    sc["elephant"][0]["allowed_paths"] = ["inventory/store.py", "tests/test_store.py", "requirements.txt"]
    asked = []
    run(demo_repo, sc, ask=lambda q: asked.append(q) or False)
    assert asked and "HIGH risk" in asked[0] and "requirements.txt" in asked[0]
    locked = ECP.load(demo_repo / ".evolution" / "ecps" / "ECP-0001.md")
    assert locked.risk == "high" and locked.risk_suggested == "low"


# ---------------------------------------------------------------- C15: risk is checked again on the real diff
def test_diff_assessment():
    assert gate.assess_diff(["a.py"], "+x\n-y\n", Config())[0] == "low"
    risk, why, hard = gate.assess_diff([f"f{i}.py" for i in range(6)], "", Config())
    assert risk == "high" and "6 files" in why[0] and not hard
    big = "\n".join("+line" for _ in range(400))
    risk, why, hard = gate.assess_diff(["a.py"], big, Config(), Governance(max_diff_lines=350))
    assert risk == "high" and hard and "above the limit" in hard[0]


def test_diff_over_the_limit_is_rolled_back(demo_repo):
    set_gov(demo_repo, max_diff_lines=2)
    res = run(demo_repo)
    assert res.outcome == "rolled_back" and any("max_diff_lines" in r for r in res.reasons)
    assert "insufficient stock" not in (demo_repo / "inventory" / "store.py").read_text()


def test_bigger_than_planned_diff_asks_a_human(demo_repo):
    set_gov(demo_repo, high_risk_diff_lines=2)
    asked = []
    run(demo_repo, ask=lambda q: asked.append(q) or True)
    assert len(asked) == 1 and "HIGH risk" in asked[0] and "lines" in asked[0]


# ---------------------------------------------------------------- C17: the lock is checked against the file on disk
def test_editing_the_ecp_file_during_the_build_aborts(demo_repo, monkeypatch):
    from evolve import pi_runner
    real = pi_runner.execute
    def sneaky(repo, ecp, cfg, actions):
        p = repo / ".evolution" / "ecps" / f"{ecp.id}.md"
        p.write_text(p.read_text().replace("inventory/store.py", "inventory/**"))
        return real(repo, ecp, cfg, actions)
    monkeypatch.setattr(pi_runner, "execute", sneaky)
    res = run(demo_repo)
    assert res.outcome == "aborted" and "on disk was changed after approval" in res.reasons[0]
    assert git(demo_repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert not git(demo_repo, "status", "--porcelain", "--", ".", ":(exclude).evolution")


def test_assert_lock_needs_the_recorded_approval(tmp_path):
    e = plan()
    lifecycle.transition(e, "reviewed", tmp_path); lifecycle.transition(e, "approved", tmp_path)
    lifecycle.assert_lock(e, tmp_path)
    (tmp_path / ".evolution" / "evidence" / "ECP-0001.jsonl").write_text("")
    with pytest.raises(lifecycle.BadTransition, match="no matching approval"):
        lifecycle.assert_lock(e, tmp_path)


# ---------------------------------------------------------------- C18: an approval names the exact plan
def test_stale_approval_for_another_plan_is_ignored(demo_repo):
    from evolve.cli import _ask_factory
    ask = _ask_factory(False, "file", demo_repo)
    run_dir = demo_repo / ".evolution" / "run"
    ans = run_dir / "approval.json"
    def human():
        for _ in range(200):
            if (run_dir / "pending_approval.json").exists():
                break
            time.sleep(0.02)
        ans.write_text(json.dumps({"approve": True, "ecp_id": "ECP-0001", "plan_hash": "OLDHASH"}))
        for _ in range(200):  # the old approval is thrown away, then the right one is given
            if not ans.exists():
                break
            time.sleep(0.05)
        ans.write_text(json.dumps({"approve": False, "ecp_id": "ECP-0001", "plan_hash": "NEWHASH", "approver": "ayush"}))
    t = threading.Thread(target=human); t.start()
    ok, how = ask("Approve?", {"ecp_id": "ECP-0001", "plan_hash": "NEWHASH"})
    t.join()
    assert ok is False and how == "human declined via dashboard by ayush"


def test_dashboard_refuses_approval_for_a_different_plan(demo_repo):
    import http.client
    from http.server import ThreadingHTTPServer
    from evolve.dashboard import server as D
    h = D.make_handler(demo_repo, auto_prepare=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    def post(body):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
        c.request("POST", "/api/approve", json.dumps(body), {"content-type": "application/json", "x-evolve-token": h.token})
        r = c.getresponse(); return r.status, json.loads(r.read())
    try:
        assert post({"approve": True})[0] == 409  # nothing pending
        run_dir = demo_repo / ".evolution" / "run"; run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "pending_approval.json").write_text(json.dumps({"question": "q", "ecp_id": "ECP-0001", "plan_hash": "H1"}))
        assert post({"approve": True, "plan_hash": "H0"})[0] == 409
        assert post({"approve": True, "plan_hash": "H1"})[0] == 200
        a = json.loads((run_dir / "approval.json").read_text())
        assert a["ecp_id"] == "ECP-0001" and a["plan_hash"] == "H1" and a["approver"]
    finally:
        srv.shutdown()


def test_gate_passes_plan_hash_to_the_approver(demo_repo):
    sc = scenario("5_high_risk_needs_approval")
    seen = []
    run(demo_repo, sc, ask=lambda q, ctx: seen.append(ctx) or (True, "human approved (test)"))
    locked = ECP.load(demo_repo / ".evolution" / "ecps" / "ECP-0001.md")
    assert [c["stage"] for c in seen] == ["plan", "result"] and all(c["plan_hash"] == locked.plan_hash for c in seen)


# ---------------------------------------------------------------- C19: the evidence head is anchored in git
def test_rewriting_the_whole_chain_is_detected_by_the_git_anchor(demo_repo):
    from evolve.decide import anchor_status
    run(demo_repo)
    assert anchor_status(demo_repo, "ECP-0001")["state"] == "ok"
    ev = Evidence(demo_repo, "ECP-0001")
    entries = ev.entries()
    entries[1]["data"]["text"] = "a request nobody made"
    prev, lines = "genesis", []
    for e in entries:  # a careful forger recomputes every hash so the chain itself still verifies
        e = {k: v for k, v in e.items() if k != "hash"}; e["prev"] = prev
        e["hash"] = hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()[:16]
        prev = e["hash"]; lines.append(json.dumps(e, ensure_ascii=False))
    ev.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ev.chain_intact()
    assert anchor_status(demo_repo, "ECP-0001")["state"] == "mismatch"


# ---------------------------------------------------------------- C20: deterministic plan checks
def _problems(demo_repo, gov=None, **kw):
    return plan_problems(plan(**kw), demo_repo, gov or Governance())


def test_good_plan_has_no_problems(demo_repo):
    assert _problems(demo_repo) == []


@pytest.mark.parametrize("kw,expect", [
    ({"allowed_paths": ["**"]}, "too broad"),
    ({"allowed_paths": ["**/*.py"]}, "too broad"),
    ({"allowed_paths": ["inventory/nothing.py", "tests/test_store.py"]}, "matches no file"),
    ({"new_files": ["docs/new.md"]}, "not covered"),
    ({"new_files": ["inventory/store.py"]}, "already exists"),
    ({"allowed_paths": ["inventory/store.py"]}, "test"),
])
def test_bad_scopes_are_caught(demo_repo, kw, expect):
    assert any(expect in p for p in _problems(demo_repo, **kw))


def test_new_files_count_as_covered(demo_repo):
    assert _problems(demo_repo, allowed_paths=["inventory/store.py", "tests/test_limits.py"], new_files=["tests/test_limits.py"]) == []


def test_too_many_files_in_scope(demo_repo):
    out = _problems(demo_repo, Governance(max_files=2), allowed_paths=["inventory/*.py", "tests/test_store.py"])
    assert any("more than the limit of 2" in p for p in out)


def test_broad_plan_is_sent_back_to_the_planner(demo_repo):
    sc = scenario("2_scope_violation_blocked")
    good = dict(sc["elephant"][0])
    sc["elephant"] = [{**good, "allowed_paths": ["**"]}, good]
    sc["goldfish"] = [{}, {}]
    res = run(demo_repo, sc)
    assert res.rounds == 2 and any(f.reviewer == "completeness" and "too broad" in f.claim for f in res.before_findings[0])
