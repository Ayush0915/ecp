from evolve.config import Config, load_config
from evolve.ecp import ECP
from evolve.gate import decide, effective_risk
from evolve.verify import verify
from tests.conftest import scenario


def ecp(**kw):
    d = dict(scenario("2_scope_violation_blocked")["elephant"][0]); d.update(kw)
    return ECP(id="ECP-0001", base_commit="x", **d)


def test_low_risk_is_automatic_and_never_asks():
    asked = []
    ok, how = decide(ecp(), Config(), "go?", lambda q: asked.append(q) or False)
    assert ok and not asked and "auto" in how


def test_high_risk_asks_human_and_respects_no():
    ok, how = decide(ecp(risk="high"), Config(), "go?", lambda q: False)
    assert not ok and "declined" in how


def test_touching_protected_path_forces_high_risk():
    cfg = Config(protected_paths=["inventory/store.py"])
    assert effective_risk(ecp(), cfg) == "high"
    assert effective_risk(ecp(allowed_paths=["docs/x.md"]), cfg) == "low"
    assert effective_risk(ecp(allowed_paths=["inventory/**"]), cfg) == "high"  # broad glob covers a protected file


def test_verify_catches_out_of_scope_edit_even_if_guard_missed_it(demo_repo):
    import subprocess
    from evolve.config import git
    base = git(demo_repo, "rev-parse", "HEAD")
    git(demo_repo, "checkout", "-q", "-b", "evolve/ECP-0001")
    (demo_repo / "inventory" / "api.py").write_text("# sneaky\n")
    e = ecp(); e.base_commit = base
    r = verify(demo_repo, e, load_config(demo_repo))
    assert r.scope_problems and not r.ok
