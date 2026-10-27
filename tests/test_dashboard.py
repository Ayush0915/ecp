"""The dashboard server, tested over real HTTP against a practice repo (scripted AI, no keys)."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from evolve.dashboard import server as D


@pytest.fixture
def api(demo_repo):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), D.make_handler(demo_repo, True, None))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return json.loads(raw) if r.headers["content-type"].startswith("application/json") else raw.decode()
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def wait_idle(timeout=90):
        end = time.time() + timeout
        time.sleep(0.5)
        while time.time() < end:
            if not call("/api/live")["running"]:
                return
            time.sleep(0.4)
        raise TimeoutError("run did not finish")

    call.wait_idle = wait_idle
    yield call
    srv.shutdown()


def test_page_is_served(api):
    html = api("/")
    assert "ECP-Governed Evolution" in html and "Run governed evolution" in html


def test_overview_lists_demo_scenarios(api):
    ov = api("/api/overview")
    assert ov["demo"] and "1_flawed_plan_caught" in ov["scenarios"] and ov["ecps"] == []


def test_run_from_dashboard_then_inspect(api):
    assert api("/api/start", {"request": "", "scenario": "1_flawed_plan_caught"})["started"]
    api.wait_idle()
    ov = api("/api/overview")
    assert ov["ecps"][0]["outcome"] == "accepted"
    d = api("/api/ecp/ECP-0001")
    kinds = [e["kind"] for e in d["evidence"]]
    assert kinds.count("review_before") == 2 and "decision" in kinds and d["chain_intact"]
    assert [v["n"] for v in d["versions"]] == [1, 2]
    assert "ECP-0001" in api("/api/file?name=ecp:ECP-0001")["text"]


def test_only_one_run_at_a_time_and_human_can_approve(api):
    assert api("/api/start", {"request": "", "scenario": "5_high_risk_needs_approval"})["started"]
    time.sleep(1.5)
    assert api("/api/start", {"request": "", "scenario": "1_flawed_plan_caught"})["started"] is False
    for _ in range(80):  # approve both gates (plan, then result)
        if api("/api/live")["pending_approval"]:
            api("/api/approve", {"approve": True})
            time.sleep(0.6)
        if not api("/api/live")["running"]:
            break
        time.sleep(0.4)
    api.wait_idle()
    assert api("/api/overview")["ecps"][0]["outcome"] == "accepted"


def test_human_can_decline_high_risk_plan(api):
    api("/api/start", {"request": "", "scenario": "5_high_risk_needs_approval"})
    for _ in range(60):
        if api("/api/live")["pending_approval"]:
            break
        time.sleep(0.3)
    assert "HIGH risk" in api("/api/live")["pending_approval"]["question"]
    api("/api/approve", {"approve": False})
    api.wait_idle()
    assert api("/api/overview")["ecps"][0]["outcome"] == "rejected"


def test_compare_from_dashboard(api):
    api("/api/compare", {"request": "", "scenario": "2_scope_violation_blocked"})
    api.wait_idle()
    c = api("/api/overview")["compare"]
    assert c["governed"]["outcome"] == "accepted" and c["plain"]["changed_files"]


def test_empty_request_rejected(api):
    assert "error" in api("/api/start", {"request": ""})


def test_file_reader_refuses_paths_outside_evolution(demo_repo):
    assert D.read_file(demo_repo, "ecp:../../etc/passwd") == ""
    assert D.read_file(demo_repo, "../../etc/passwd") == ""
    assert "Rules" in D.read_file(demo_repo, "rules")
