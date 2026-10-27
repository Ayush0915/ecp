"""End-to-end: the three demo scenarios, run for real against a temporary git repo."""
from evolve import engine
from evolve.config import git, load_config
from evolve.ecp import ECP, Evidence
from evolve.llm import MockLLM
from tests.conftest import scenario


def run(repo, name, ask=lambda q: True):
    sc = scenario(name)
    return engine.run(repo, sc["request"], MockLLM(sc), load_config(repo), sc.get("pi_actions"), ask)


def test_flawed_plan_is_caught_revised_then_accepted(demo_repo):
    res = run(demo_repo, "1_flawed_plan_caught")
    assert res.outcome == "accepted" and res.rounds == 2
    assert any(f.reviewer == "root_cause" and f.severity == "blocker" for f in res.before_findings[0])
    assert "insufficient stock" in (demo_repo / "inventory" / "store.py").read_text()
    assert git(demo_repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "Merge ECP-0001" in git(demo_repo, "log", "--oneline", "-5")


def test_out_of_scope_edits_are_blocked_and_logged(demo_repo):
    res = run(demo_repo, "2_scope_violation_blocked")
    assert res.outcome == "accepted"
    assert {b["path"] for b in res.blocked_edits} == {"inventory/api.py", ".evolution/rules.md"}
    assert "agent got creative" not in (demo_repo / "inventory" / "api.py").read_text()
    assert "Anything goes" not in (demo_repo / ".evolution" / "rules.md").read_text()
    ev = Evidence(demo_repo, "ECP-0001")
    assert ev.chain_intact()
    blocked = [e for e in ev.entries() if e["kind"] == "pi_action" and not e["data"]["allowed"]]
    assert len(blocked) == 2


def test_breaking_change_is_rolled_back_cleanly(demo_repo):
    before = (demo_repo / "inventory" / "store.py").read_text()
    res = run(demo_repo, "3_bad_change_rolled_back")
    assert res.outcome == "rolled_back" and "tests failed" in res.reasons
    assert (demo_repo / "inventory" / "store.py").read_text() == before
    assert git(demo_repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "evolve/ECP-0001" not in git(demo_repo, "branch")
    assert not git(demo_repo, "status", "--porcelain", "--", ".", ":(exclude).evolution")


def test_history_is_remembered_for_next_plan(demo_repo):
    run(demo_repo, "3_bad_change_rolled_back")
    from evolve.elephant import history
    h = history(demo_repo)
    assert "ECP-0001" in h and "rolled_back" in h


def test_second_run_gets_next_id(demo_repo):
    run(demo_repo, "2_scope_violation_blocked")
    res = run(demo_repo, "3_bad_change_rolled_back")
    assert res.ecp.id == "ECP-0002"


def test_high_risk_plan_declined_by_human_is_never_executed(demo_repo):
    sc = scenario("2_scope_violation_blocked")
    sc["elephant"][0]["risk"] = "high"
    res = engine.run(demo_repo, sc["request"], MockLLM(sc), load_config(demo_repo), sc["pi_actions"], lambda q: False)
    assert res.outcome == "rejected"
    assert "insufficient stock" not in (demo_repo / "inventory" / "store.py").read_text()
    assert "evolve/ECP-0001" not in git(demo_repo, "branch")


def test_plan_that_never_gets_fixed_is_rejected_after_max_rounds(demo_repo):
    sc = scenario("1_flawed_plan_caught")
    sc["elephant"] = [sc["elephant"][0]]  # Elephant keeps resubmitting the flawed plan
    sc["goldfish"] = [sc["goldfish"][0]]  # and Goldfish keeps finding the same problems
    res = engine.run(demo_repo, sc["request"], MockLLM(sc), load_config(demo_repo), sc["pi_actions"], lambda q: True)
    assert res.outcome == "rejected" and res.rounds == 4


def test_dirty_repo_refused(demo_repo):
    (demo_repo / "inventory" / "store.py").write_text("# local edit\n")
    import pytest
    with pytest.raises(RuntimeError, match="uncommitted"):
        run(demo_repo, "1_flawed_plan_caught")


def test_correctness_blocker_after_green_tests_still_rolls_back(demo_repo):
    sc = scenario("2_scope_violation_blocked")
    sc["goldfish_post"] = [{"correctness": [{"severity": "blocker", "location": "x", "claim": "logic is wrong", "suggested_fix": ""}]}]
    res = engine.run(demo_repo, sc["request"], MockLLM(sc), load_config(demo_repo), sc["pi_actions"], lambda q: True)
    assert res.outcome == "rolled_back"


# ---------------------------------------------------------------- fail-before / pass-after
def test_bug_fix_without_a_test_is_rolled_back(demo_repo):
    res = run(demo_repo, "4_fix_without_proof")
    assert res.outcome == "rolled_back"
    assert any("no proof" in r and "test" in r for r in res.reasons)
    assert "insufficient stock" not in (demo_repo / "inventory" / "store.py").read_text()


def test_test_that_passes_on_old_code_is_not_proof(demo_repo):
    sc = scenario("2_scope_violation_blocked")
    sc["pi_actions"] = [sc["pi_actions"][0],  # real fix
                        {"tool": "edit", "path": "tests/test_store.py", "old": '        s.remove_stock("apple", -1)\n',
                         "new": '        s.remove_stock("apple", -1)\n\n\ndef test_trivially_true():\n    assert Store().level("x") == 0\n'}]
    res = engine.run(demo_repo, sc["request"], MockLLM(sc), load_config(demo_repo), sc["pi_actions"], lambda q: True)
    assert res.outcome == "rolled_back" and any("PASS on the old code" in r for r in res.reasons)


def test_proof_is_recorded_and_tree_restored(demo_repo):
    res = run(demo_repo, "1_flawed_plan_caught")
    ev = [e for e in Evidence(demo_repo, "ECP-0001").entries() if e["kind"] == "verify"][0]
    assert ev["data"]["fail_before"]["status"] == "proved"
    assert "insufficient stock" in (demo_repo / "inventory" / "store.py").read_text()  # fix is back after the check


def test_enhancements_do_not_need_the_proof(demo_repo):
    res = run(demo_repo, "3_bad_change_rolled_back")
    ev = [e for e in Evidence(demo_repo, "ECP-0001").entries() if e["kind"] == "verify"][0]
    assert ev["data"]["fail_before"]["status"] == "skipped"


# ---------------------------------------------------------------- plan versions / insufficient context
def test_every_plan_version_is_kept(demo_repo):
    from evolve.ecp import list_versions
    run(demo_repo, "1_flawed_plan_caught")
    vs = list_versions(demo_repo, "ECP-0001")
    assert [n for n, _ in vs] == [1, 2]
    assert "inventory/api.py" in vs[0][1] and "inventory/store.py" in vs[1][1]


def test_insufficient_context_finding_stops_the_plan(demo_repo):
    sc = scenario("2_scope_violation_blocked")
    sc["goldfish"] = [{"readiness": [{"severity": "major", "location": "ECP", "claim": "INSUFFICIENT CONTEXT: the file that defines Store.level was not provided.", "suggested_fix": "Allow reading it."}]}, {}]
    sc["elephant"] = [sc["elephant"][0], sc["elephant"][0]]
    res = engine.run(demo_repo, sc["request"], MockLLM(sc), load_config(demo_repo), sc["pi_actions"], lambda q: True)
    assert res.rounds == 2 and res.before_findings[0][0].claim.startswith("INSUFFICIENT CONTEXT")


# ---------------------------------------------------------------- comparison + triggers
def test_compare_shows_plain_agent_collateral_damage_and_leaves_repo_clean(demo_repo):
    from evolve import compare as cmp
    sc = scenario("2_scope_violation_blocked")
    out = cmp.compare(demo_repo, sc["request"], MockLLM(sc), load_config(demo_repo), sc["plain_actions"], sc["pi_actions"])
    assert not out["plain"]["tests_ok"] and "inventory/api.py" in out["plain"]["changed_files"]  # the unguarded agent broke the API
    assert ".evolution/rules.md" in out["plain"]["changed_files"]
    assert set(out["plain_outside_governed_scope"]) >= {"inventory/api.py", ".evolution/rules.md"}
    assert out["governed"]["outcome"] == "accepted" and len(out["governed"]["blocked_attempts"]) == 2
    assert "Anything goes" not in (demo_repo / ".evolution" / "rules.md").read_text()  # plain run was fully undone
    assert (demo_repo / ".evolution" / "compare" / "latest.json").exists()
    assert "PLAIN AGENT" in cmp.format_comparison(out)


def test_failing_tests_trigger_builds_a_request(demo_repo):
    from evolve.cli import failing_tests_request
    (demo_repo / "tests" / "test_broken.py").write_text("def test_x():\n    assert 1 == 2\n")
    git(demo_repo, "add", "-A"); git(demo_repo, "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", "broken test")
    req = failing_tests_request(demo_repo, load_config(demo_repo))
    assert "test suite is failing" in req and "assert 1 == 2" in req


def test_file_approval_handshake(demo_repo):
    import json, threading, time
    from evolve.cli import _ask_factory
    ask = _ask_factory(False, "file", demo_repo)
    run_dir = demo_repo / ".evolution" / "run"
    def human():
        for _ in range(100):
            if (run_dir / "pending_approval.json").exists():
                (run_dir / "approval.json").write_text(json.dumps({"approve": True})); return
            time.sleep(0.05)
    t = threading.Thread(target=human); t.start()
    assert ask("Approve?") is True
    t.join()
