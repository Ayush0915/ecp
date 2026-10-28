"""Discovery: signals from plain programs, Elephant scout, checks, scoring, files, and handing a finding to the core."""
import importlib.util
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from evolve import discover as D
from evolve import engine
from evolve.config import init_repo, load_config
from evolve.dashboard import server as dash
from tests.conftest import DEMO, make_repo
from evolve.llm import MockLLM

ROOT = Path(__file__).resolve().parent.parent
SCEN = json.loads((DEMO / "discovery" / "shop.json").read_text())
needs_tools = pytest.mark.skipif(not all(importlib.util.find_spec(m) for m in ("bandit", "ruff", "coverage")), reason="bandit, ruff, coverage not installed")


@pytest.fixture
def shop(tmp_path):
    return make_repo(tmp_path / "shop", "shop_app")


def run_discovery(repo):
    cfg = load_config(repo)
    return D.discover(repo, MockLLM(SCEN), cfg)


def test_quote_check_is_exact_and_plain(shop):
    assert D.quote_line(shop, "shop/pricing.py", "return price - price * percent / 100") == 7
    assert D.quote_line(shop, "shop/pricing.py", "this line does not exist") == 0
    assert D.quote_line(shop, "shop/nope.py", "x") == 0


def test_only_same_metric_signals_count_as_evidence():
    sigs = {"S-1": {"id": "S-1", "metric": "quality", "file": "a.py", "line": 5, "lines": [], "kind": "tool"}}
    f = {"file": "a.py", "metric": "security", "signal_ids": ["S-1"]}
    assert D.linked_signals(f, 5, sigs) == []                      # a style warning is not evidence of a security bug
    assert D.evidence_level([])[0] == 1
    f["metric"] = "quality"
    assert D.evidence_level(D.linked_signals(f, 6, sigs))[0] == 2  # near the same spot, same metric
    assert D.linked_signals(f, 50, sigs)[0]["id"] == "S-1"          # the model named it, same file and metric
    assert D.evidence_level([{"kind": "reproduced"}])[0] == 3


@needs_tools
def test_shop_discovery_end_to_end(shop):
    s = run_discovery(shop)
    titles = {f["title"]: f for f in s["findings"]}
    assert len(s["findings"]) == 6 and len(s["dropped"]) == 2
    reasons = " ".join(d["why"] for d in s["dropped"])
    assert "not found in that file" in reasons and "independent reviewer" in reasons
    assert titles["Admin password is written in the source code"]["evidence"] == 2
    assert titles["A discount above 100% makes the price negative"]["evidence"] == 1   # no tool backs it, so it is honest about that
    assert titles["find_duplicates compares every pair of items"]["measurable"] is False
    ranks = [(-f["evidence"], -D.IMPACT[f["impact"]], D.SIZE[f["fix_size"]]) for f in s["findings"]]
    assert ranks == sorted(ranks)
    for name in ("ran",):
        assert s["detectors"]["security (bandit)"]["state"] == name
    for f in s["findings"]:
        assert (shop / ".evolution" / "findings" / f"{f['id']}.json").exists()
    assert (shop / ".evolution" / "baseline.json").exists() and (shop / ".evolution" / "discovery.jsonl").exists()


def test_missing_tool_is_skipped_not_fatal(shop, monkeypatch):
    monkeypatch.setattr(D, "_tool", lambda repo, args, timeout=300: (1, "", f"No module named {args[0]}"))
    cfg = load_config(shop)
    sig = D.collect_signals(shop, cfg)
    assert sig.detectors["security (bandit)"]["state"] == "skipped" and "pip install bandit" in sig.detectors["security (bandit)"]["note"]
    assert sig.detectors["quality (ruff)"]["state"] == "skipped"


def test_failing_tests_become_reproduced_signals(shop):
    (shop / "tests" / "test_pricing.py").write_text("def test_boom():\n    assert 1 == 2\n", encoding="utf-8")
    sig = D.collect_signals(shop, load_config(shop))
    bad = [s for s in sig.items if s["metric"] == "correctness"]
    assert bad and bad[0]["kind"] == "reproduced"


def test_unreadable_claim_check_fails_closed(shop):
    class Bad(MockLLM):
        def complete(self, role, system, user, model=None):
            return "not json" if role == "goldfish:claim" else super().complete(role, system, user, model)
    s = D.discover(shop, Bad(SCEN), load_config(shop))
    assert s["findings"] == [] and len(s["dropped"]) == 8


@needs_tools
def test_a_finding_goes_through_the_whole_core(shop):
    s = run_discovery(shop)
    f = next(x for x in s["findings"] if x["metric"] == "correctness")
    cfg = load_config(shop)
    res = engine.run(shop, D.request_from_finding(f), MockLLM(SCEN), cfg, SCEN["pi_actions"], lambda q: True, finding=f)
    assert res.outcome == "accepted"
    assert D.load_finding(shop, f["id"])["status"] == "accepted" and D.load_finding(shop, f["id"])["ecp"] == res.ecp.id
    kinds = [json.loads(l)["kind"] for l in (shop / ".evolution" / "evidence" / f"{res.ecp.id}.jsonl").read_text().splitlines()]
    assert "finding" in kinds and "decision" in kinds
    with pytest.raises(ValueError):
        D.load_finding(shop, "F-999")


@needs_tools
def test_dashboard_discover_then_evolve(shop):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), dash.make_handler(shop, auto_prepare=False, test_scenarios=DEMO))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def idle():
        time.sleep(0.5)
        for _ in range(200):
            if not call("/api/live")["running"]:
                return
            time.sleep(0.4)
        raise TimeoutError

    try:
        assert call("/api/discover", {"scenario": "discovery/shop"})["started"]
        idle()
        ov = call("/api/overview")
        assert len(ov["findings"]) == 6 and ov["baseline"]["detectors"]["tests"]["state"] == "ran"
        assert "error" in call("/api/start", {"finding": "F-404", "scenario": "discovery/shop"})
        fid = next(f["id"] for f in ov["findings"] if f["metric"] == "correctness")
        assert call("/api/start", {"finding": fid, "scenario": "discovery/shop"})["started"]
        idle()
        ov = call("/api/overview")
        assert ov["ecps"][0]["outcome"] == "accepted" and next(f for f in ov["findings"] if f["id"] == fid)["status"] == "accepted"
    finally:
        srv.shutdown()
