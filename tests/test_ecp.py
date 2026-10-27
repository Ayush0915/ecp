import pytest

from evolve.ecp import ECP, Evidence
from evolve.lifecycle import BadTransition, transition
from tests.conftest import scenario


def good():
    d = scenario("2_scope_violation_blocked")["elephant"][0]
    return ECP(id="ECP-0001", base_commit="abc", **d)


def test_roundtrip_markdown():
    e = good()
    assert ECP.from_markdown(e.to_markdown()) == e


def test_incomplete_ecp_reports_problems():
    e = ECP(id="ECP-0001", title="t", change_type="bug", intent="short")
    probs = e.problems()
    assert any("allowed_paths" in p for p in probs) and any("verification" in p for p in probs)


def test_overlap_detected():
    e = good(); e.forbidden_paths = list(e.allowed_paths)
    assert any("both allowed and forbidden" in p for p in e.problems())


def test_lock_detects_tampering():
    e = good(); e.lock()
    assert e.lock_intact()
    e.allowed_paths.append("**")  # someone widens the scope after approval
    assert not e.lock_intact()


def test_status_does_not_break_lock():
    e = good(); e.lock(); e.status = "executing"
    assert e.lock_intact()


def test_evidence_chain_and_tamper(tmp_path):
    ev = Evidence(tmp_path, "ECP-0001")
    ev.add("a"); ev.add("b", {"x": 1}); ev.add("c")
    assert ev.chain_intact()
    lines = ev.path.read_text().splitlines()
    lines[1] = lines[1].replace('"x": 1', '"x": 2')
    ev.path.write_text("\n".join(lines) + "\n")
    assert not ev.chain_intact()


def test_lifecycle_order_enforced(tmp_path):
    e = good()
    with pytest.raises(BadTransition):
        transition(e, "executing", tmp_path)  # skipping review/approval
    transition(e, "reviewed", tmp_path)
    transition(e, "approved", tmp_path)
    assert e.is_locked()


def test_cannot_approve_incomplete(tmp_path):
    e = ECP(id="ECP-0002", title="t", change_type="bug", intent="a long enough intent here")
    transition(e, "reviewed", tmp_path)
    with pytest.raises(BadTransition):
        transition(e, "approved", tmp_path)


def test_cannot_execute_if_plan_changed_after_lock(tmp_path):
    e = good()
    transition(e, "reviewed", tmp_path); transition(e, "approved", tmp_path)
    e.allowed_paths.append("**")
    with pytest.raises(BadTransition):
        transition(e, "executing", tmp_path)
