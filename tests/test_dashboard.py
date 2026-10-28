"""The dashboard server, tested over real HTTP (scripted AI, no keys, no network)."""
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from evolve.dashboard import server as D
from tests.conftest import DEMO


def client(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
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

    call.wait_idle, call.stop = wait_idle, srv.shutdown
    return call


@pytest.fixture
def api(demo_repo):
    c = client(D.make_handler(demo_repo, auto_prepare=False, test_scenarios=DEMO))
    yield c
    c.stop()


def test_page_is_served(api):
    html = api("/")
    assert "Evolve" in html and "Discover problems" in html and "/api/events" in html and "/api/ingest" in html


def test_run_from_dashboard_then_inspect(api):
    assert api("/api/start", {"request": "", "scenario": "scenarios/1_flawed_plan_caught"})["started"]
    api.wait_idle()
    ov = api("/api/overview")
    assert ov["connected"] and ov["repo"]["name"] and ov["ecps"][0]["outcome"] == "accepted"
    d = api("/api/ecp/ECP-0001")
    kinds = [e["kind"] for e in d["evidence"]]
    assert kinds.count("review_before") == 2 and "decision" in kinds and d["chain_intact"]
    assert [v["n"] for v in d["versions"]] == [1, 2]
    assert "ECP-0001" in api("/api/file?name=ecp:ECP-0001")["text"]
    feed = api("/api/events?ecp=ECP-0001")
    assert {"elephant_done", "goldfish_done", "pi_action", "verify_done", "decision"} <= {e["kind"] for e in feed}


def test_only_one_run_at_a_time_and_human_can_approve(api):
    assert api("/api/start", {"request": "", "scenario": "scenarios/5_high_risk_needs_approval"})["started"]
    time.sleep(1.5)
    assert api("/api/start", {"request": "", "scenario": "scenarios/1_flawed_plan_caught"})["started"] is False
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
    api("/api/start", {"request": "", "scenario": "scenarios/5_high_risk_needs_approval"})
    for _ in range(60):
        if api("/api/live")["pending_approval"]:
            break
        time.sleep(0.3)
    assert "HIGH risk" in api("/api/live")["pending_approval"]["question"]
    api("/api/approve", {"approve": False})
    api.wait_idle()
    assert api("/api/overview")["ecps"][0]["outcome"] == "rejected"


def test_empty_request_rejected(api):
    assert "error" in api("/api/start", {"request": ""})


def test_file_reader_refuses_paths_outside_evolution(demo_repo):
    assert D.read_file(demo_repo, "ecp:../../etc/passwd") == ""
    assert D.read_file(demo_repo, "../../etc/passwd") == ""
    assert "Rules" in D.read_file(demo_repo, "rules")


def test_azure_settings_are_saved_to_env_and_secrets_are_never_returned(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "ENV_FILE", tmp_path / ".env")
    for k in D.PLAIN + D.SECRET:
        monkeypatch.delenv(k, raising=False)
    c = client(D.make_handler(None, auto_prepare=False))
    try:
        assert c("/api/azure")["auth"] == "none"
        assert "error" in c("/api/azure", {"AZURE_OPENAI_BASE": "http://not-https"})
        out = c("/api/azure", {"AZURE_OPENAI_BASE": "https://r.openai.azure.com", "AZURE_OPENAI_DEPLOYMENT": "gpt-5.5", "AZURE_OPENAI_API_KEY": "s3cret-value"})
        assert out["auth"] == "key" and out["secrets"]["AZURE_OPENAI_API_KEY"] is True and "s3cret-value" not in json.dumps(out)
        env = (tmp_path / ".env").read_text()
        assert "AZURE_OPENAI_BASE=https://r.openai.azure.com" in env and "AZURE_OPENAI_API_KEY=s3cret-value" in env
        out = c("/api/azure", {"AZURE_OPENAI_DEPLOYMENT": "other"})                      # an empty secret box means "unchanged"
        assert out["secrets"]["AZURE_OPENAI_API_KEY"] is True and out["values"]["AZURE_OPENAI_DEPLOYMENT"] == "other"
        out = c("/api/azure", {"clear": ["AZURE_OPENAI_API_KEY"]})
        assert out["secrets"]["AZURE_OPENAI_API_KEY"] is False and "API_KEY" not in (tmp_path / ".env").read_text()
    finally:
        c.stop()
        for k in D.PLAIN + D.SECRET:
            monkeypatch.delenv(k, raising=False)


def test_settings_endpoint(demo_repo):
    c = client(D.make_handler(demo_repo, auto_prepare=False))
    try:
        r = c("/api/settings", {"goldfish": "gpt-5.5-mini", "test_command": "pytest -q"})
        assert r["goldfish"] == "gpt-5.5-mini" and c("/api/settings")["test_command"] == "pytest -q"
        assert "error" in c("/api/settings", {"sandbox": "docker"})
    finally:
        c.stop()


def test_connect_a_local_repo_and_watch_the_ingest(tmp_path, demo_repo):
    c = client(D.make_handler(None, auto_prepare=False, workdir=tmp_path / "ws"))
    try:
        assert c("/api/overview")["connected"] is False
        assert "error" in c("/api/start", {"request": "x"})
        assert "error" in c("/api/connect", {"repo": ""})                 # bad input is refused at once
        assert "error" in c("/api/connect", {"repo": "-oProxyCommand=x"})
        assert c("/api/connect", {"repo": str(tmp_path / "nothing")})["started"]
        for _ in range(50):
            if c("/api/ingest")["state"] != "working":
                break
            time.sleep(0.1)
        assert c("/api/ingest")["state"] == "error"                       # not a folder: the message says so
        assert c("/api/connect", {"repo": str(demo_repo)})["started"]
        for _ in range(100):
            if c("/api/ingest")["state"] != "working":
                break
            time.sleep(0.1)
        assert c("/api/ingest")["state"] == "done"
        ov = c("/api/overview")
        assert ov["connected"] and ov["repo"]["name"] == "repo" and ov["repo"]["languages"][0]["name"] == "Python"
        assert any("Cloning" in l or "Using the local" in l for l in c("/api/ingest")["lines"])
    finally:
        c.stop()
