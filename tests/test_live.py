"""The live feed that the dashboard draws from."""
from evolve import engine, live
from evolve.config import load_config
from evolve.llm import MockLLM
from tests.conftest import scenario


def test_emit_read_and_never_raises(tmp_path):
    live.reset(tmp_path, "run", "x")
    live.emit(tmp_path, "elephant_start", task="draft")
    evs = live.read(tmp_path)
    assert [e["kind"] for e in evs] == ["job_start", "elephant_start"] and [e["seq"] for e in evs] == [1, 2]
    assert live.read(tmp_path, after=1)[0]["kind"] == "elephant_start"
    live.emit("/nonexistent/\0bad", "x")  # must not raise


def test_a_run_tells_the_whole_story_and_is_saved_for_replay(demo_repo):
    cfg = load_config(demo_repo)
    live.reset(demo_repo, "run")
    res = engine.run(demo_repo, "Stop stock going negative", MockLLM(scenario("1_flawed_plan_caught")), cfg, pi_actions=scenario("1_flawed_plan_caught")["pi_actions"], ask=lambda q: True)
    kinds = [e["kind"] for e in live.read(demo_repo)]
    for k in ("elephant_start", "elephant_done", "review_start", "goldfish_start", "goldfish_done", "review_done", "gate", "locked", "pi_start", "pi_action", "verify_step", "verify_done", "decision"):
        assert k in kinds, k
    # every Goldfish that started also finished
    assert kinds.count("goldfish_start") == kinds.count("goldfish_done")
    done = [e for e in live.read(demo_repo) if e["kind"] == "elephant_done"][0]["data"]["ecp"]
    assert done["allowed_paths"] and done["title"]
    assert live.read(demo_repo, ecp=res.ecp.id), "the feed is kept per run"
