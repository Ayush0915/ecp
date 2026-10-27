from evolve.config import load_config
from evolve.ecp import ECP
from evolve.goldfish import blocking, review, roster
from evolve.goldfish.runner import build_context, render_context
from evolve.llm import MockLLM, extract_json
from tests.conftest import scenario


def ecp_for(name):
    return ECP(id="ECP-0001", base_commit="x", **scenario(name)["elephant"][-1])


def test_roster_by_change_type():
    bug = {l.name for l in roster("bug", "before")}
    enh = {l.name for l in roster("enhancement", "before")}
    assert "root_cause" in bug and "root_cause" not in enh and "impact" in enh
    assert {l.name for l in roster("bug", "after")} == {"conformance", "correctness"}


def test_correctness_uses_different_model(demo_repo):
    cfg = load_config(demo_repo)
    llm = MockLLM(scenario("2_scope_violation_blocked"))
    review(llm, cfg, demo_repo, ecp_for("2_scope_violation_blocked"), "after")
    models = {c["role"]: c["model"] for c in llm.calls}
    assert models["goldfish:correctness"] != models["goldfish:conformance"]


def test_context_contract_goldfish_sees_only_ecp_rules_and_named_files(demo_repo):
    ecp = ecp_for("2_scope_violation_blocked")
    d = build_context(demo_repo, ecp, "before")
    text = render_context(d)
    assert "===== ECP.md =====" in text and "===== RULES.md =====" in text
    assert "files/inventory/store.py" in text and "files/tests/test_store.py" in text
    assert "files/inventory/api.py" not in text


def test_goldfish_never_sees_request_or_elephant_reasoning(demo_repo):
    cfg = load_config(demo_repo)
    sc = scenario("1_flawed_plan_caught")
    llm = MockLLM(sc)
    review(llm, cfg, demo_repo, ecp_for("1_flawed_plan_caught"), "before")
    for c in llm.calls:
        assert sc["request"] not in c["user"]
        assert "CHANGE REQUEST" not in c["user"]


def test_findings_and_severity(demo_repo):
    cfg = load_config(demo_repo)
    llm = MockLLM(scenario("1_flawed_plan_caught"))
    fs = review(llm, cfg, demo_repo, ecp_for("1_flawed_plan_caught"), "before")
    assert {f.reviewer for f in fs} == {"root_cause", "intent_fit", "verification"}
    assert {f.severity for f in blocking(fs)} == {"blocker", "major"}


def test_unreadable_reviewer_output_fails_closed(demo_repo):
    class Garbage:
        def complete(self, role, system, user, model=None):
            return "I think it looks fine!"
    fs = review(Garbage(), load_config(demo_repo), demo_repo, ecp_for("2_scope_violation_blocked"), "before")
    assert fs and all(f.severity == "major" for f in fs)


def test_extract_json_array_vs_object():
    assert extract_json('[{"a": 1}]') == [{"a": 1}]
    assert extract_json('text ```json\n{"a": 1}\n``` more') == {"a": 1}
