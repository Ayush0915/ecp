"""The 'live' code paths, exercised against local stand-ins (no internet, no keys):
  - a fake Azure OpenAI server (checks URL, auth header, body; replays a demo scenario)
  - a fake `pi` program (checks how Pi is launched and how its action log is read back)
  - the exact podman command line
"""
import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from evolve import engine
from evolve.config import load_config
from evolve.ecp import ECP
from evolve.llm import MockLLM, make_llm
from evolve.pi_runner import podman_command, run_pi
from tests.conftest import scenario

LENS_NAMES = ["comprehension", "intent_fit", "verification", "scope_rules", "readiness", "root_cause", "impact",
              "behavior_preserved", "conformance", "correctness", "security", "performance"]


class FakeAzure(BaseHTTPRequestHandler):
    scenario_llm: MockLLM = None
    seen: list = []

    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        FakeAzure.seen.append({"path": self.path, "key": self.headers.get("api-key"), "model": body["model"]})
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if system.startswith("You are Elephant"):
            role = "elephant:revise" if "YOUR CURRENT ECP" in user else "elephant:draft"
        else:
            lens_dir = Path(__file__).resolve().parent.parent / "evolve" / "goldfish" / "lenses"
            role = "goldfish:" + next(n for n in LENS_NAMES if (lens_dir / f"{n}.md").read_text().strip() in system)
        text = FakeAzure.scenario_llm.complete(role, system, user)
        out = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
        self.send_response(200); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)


def test_azure_provider_end_to_end_against_fake_server(demo_repo, monkeypatch):
    sc = scenario("1_flawed_plan_caught")
    FakeAzure.scenario_llm, FakeAzure.seen = MockLLM(sc), []
    srv = HTTPServer(("127.0.0.1", 0), FakeAzure)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", f"http://127.0.0.1:{srv.server_port}")
    p = demo_repo / ".evolution" / "config.yaml"
    c = yaml.safe_load(p.read_text())
    c["models"].update(provider="azure", elephant="gpt-5.5", goldfish="gpt-5.5", correctness="gpt-5.5", api_key_env="AZURE_OPENAI_API_KEY")
    p.write_text(yaml.safe_dump(c))
    cfg = load_config(demo_repo)
    res = engine.run(demo_repo, sc["request"], make_llm(cfg), cfg, sc["pi_actions"], lambda q: True)
    srv.shutdown()
    assert res.outcome == "accepted" and res.rounds == 2          # same story as the offline demo, over HTTP
    assert all(s["path"] == "/openai/v1/chat/completions" for s in FakeAzure.seen)
    assert all(s["key"] == "secret-key" and s["model"] == "gpt-5.5" for s in FakeAzure.seen)


def test_azure_requires_env(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    from evolve.config import Config
    cfg = Config(); cfg.models.provider = "azure"
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_API_KEY"):
        make_llm(cfg)


def test_podman_command_exposes_only_what_it_should(demo_repo, tmp_path):
    cfg = load_config(demo_repo); cfg.pi.sandbox, cfg.pi.model = "podman", "azure/gpt-5.5"
    ecp = ECP(id="ECP-0001", base_commit="x", **scenario("2_scope_violation_blocked")["elephant"][0])
    cmd = podman_command(demo_repo, tmp_path, ecp, cfg, "PROMPT")
    joined = " ".join(cmd)
    assert cmd[:2] == ["podman", "run"] and "--cap-drop=ALL" in cmd and "no-new-privileges" in joined
    mounts = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
    assert len(mounts) == 3 and mounts[1].endswith("/guard:ro,Z")      # repo, guard (read-only), run folder. Nothing else.
    assert "--tools" in cmd and cmd[cmd.index("--tools") + 1] == "read,grep,find,ls,edit,write"   # no bash
    assert "--model" in cmd and "azure/gpt-5.5" in cmd and cmd[-1] == "PROMPT"
    assert "secret" not in joined


def test_api_key_is_passed_by_name_not_value(demo_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "super-secret-value")
    cfg = load_config(demo_repo)
    ecp = ECP(id="ECP-0001", base_commit="x", **scenario("2_scope_violation_blocked")["elephant"][0])
    cmd = podman_command(demo_repo, tmp_path, ecp, cfg, "P")
    assert "AZURE_OPENAI_API_KEY" in cmd and "super-secret-value" not in " ".join(cmd)


FAKE_PI = f'''#!{sys.executable}
import json, os, sys
log = os.environ["ECP_ACTION_LOG"]; scope = json.load(open(os.environ["ECP_SCOPE_FILE"]))
assert "-e" in sys.argv and "--no-session" in sys.argv and "--mode" in sys.argv
def act(tool, path, allowed, reason="ok"):
    open(log, "a").write(json.dumps({{"tool": tool, "path": path, "allowed": allowed, "reason": reason}}) + "\\n")
p = "inventory/store.py"
assert p in scope["allowed"]
s = open(p).read().replace("        self._stock[sku] = self._stock.get(sku, 0) - qty", "        if self._stock.get(sku, 0) < qty:\\n            raise ValueError('insufficient stock')\\n        self._stock[sku] = self._stock.get(sku, 0) - qty")
open(p, "w").write(s); act("edit", p, True)
act("write", "inventory/api.py", False, "forbidden")
'''


def test_real_pi_launch_plumbing_with_fake_pi(demo_repo, tmp_path, monkeypatch):
    fake = tmp_path / "bin"; fake.mkdir()
    (fake / "pi").write_text(FAKE_PI); (fake / "pi").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    cfg = load_config(demo_repo); cfg.pi.backend = "pi"
    ecp = ECP(id="ECP-0001", base_commit="x", **scenario("2_scope_violation_blocked")["elephant"][0])
    res = run_pi(demo_repo, ecp, cfg)
    assert not res.error, res.error
    assert [a["path"] for a in res.applied] == ["inventory/store.py"]
    assert [b["path"] for b in res.blocked] == ["inventory/api.py"]
    assert "insufficient stock" in (demo_repo / "inventory" / "store.py").read_text()


def test_missing_podman_gives_clear_error(demo_repo, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    cfg = load_config(demo_repo); cfg.pi.backend, cfg.pi.sandbox = "pi", "podman"
    ecp = ECP(id="ECP-0001", base_commit="x", **scenario("2_scope_violation_blocked")["elephant"][0])
    assert "podman is not installed" in run_pi(demo_repo, ecp, cfg).error


def test_report_is_written_and_shows_the_story(demo_repo):
    sc = scenario("2_scope_violation_blocked")
    engine.run(demo_repo, sc["request"], MockLLM(sc), load_config(demo_repo), sc["pi_actions"], lambda q: True)
    page = (demo_repo / ".evolution" / "reports" / "ECP-0001.html").read_text()
    assert "ACCEPTED" in page and "BLOCKED" in page and "inventory/api.py" in page and "intact" in page


def test_azure_endpoint_is_translated_for_pi(demo_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://r.openai.azure.com")
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    cfg = load_config(demo_repo)
    ecp = ECP(id="ECP-0001", base_commit="x", **scenario("2_scope_violation_blocked")["elephant"][0])
    assert "AZURE_OPENAI_BASE_URL=https://r.openai.azure.com" in podman_command(demo_repo, tmp_path, ecp, cfg, "P")


# ---------------------------------------------------------------- doctor
def test_doctor_reports_unset_repo(tmp_path):
    from evolve.cli import doctor_checks
    rows = doctor_checks(tmp_path)
    assert any((not ok) and "not set up" in name for ok, name, _ in rows)


def test_doctor_flags_missing_azure_env_and_failing_baseline(demo_repo, monkeypatch):
    from evolve.cli import doctor_checks
    for k in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_RESOURCE_NAME"):
        monkeypatch.delenv(k, raising=False)
    p = demo_repo / ".evolution" / "config.yaml"
    c = yaml.safe_load(p.read_text()); c["models"]["provider"] = "azure"; c["test_command"] = "false"; p.write_text(yaml.safe_dump(c))
    bad = {name for ok, name, _ in doctor_checks(demo_repo) if not ok}
    assert "AZURE_OPENAI_API_KEY is set" in bad and "tests pass BEFORE any change (baseline)" in bad


def test_doctor_all_green_on_demo_repo(demo_repo):
    from evolve.cli import doctor_checks
    assert all(ok for ok, *_ in doctor_checks(demo_repo))


def test_doctor_catches_dirty_tree(demo_repo):
    from evolve.cli import doctor_checks
    (demo_repo / "inventory" / "store.py").write_text("# edited\n")
    assert any((not ok) and "clean" in name for ok, name, _ in doctor_checks(demo_repo, run_tests=False))


# ---------------------------------------------------------------- azure details
def test_azure_accepts_pis_env_names_and_tidies_the_url(monkeypatch):
    from evolve.llm import AzureOpenAILLM
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://res.openai.azure.com/openai/v1/")
    assert AzureOpenAILLM("gpt-5.5", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY").endpoint == "https://res.openai.azure.com"
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL")
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE_NAME", "myres")
    assert AzureOpenAILLM("gpt-5.5", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY").endpoint == "https://myres.openai.azure.com"


def test_rate_limit_is_retried_then_succeeds(monkeypatch):
    from evolve import llm
    hits = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            hits.append(1)
            code, out = (429, b'{"error":"slow down"}') if len(hits) < 3 else (200, b'{"ok": true}')
            self.send_response(code); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    assert llm._post(f"http://127.0.0.1:{srv.server_port}/x", {}, {}) == {"ok": True}
    assert len(hits) == 3
    srv.shutdown()


def test_hard_error_message_is_readable(monkeypatch):
    from evolve import llm

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            out = b'{"error":{"message":"DeploymentNotFound"}}'
            self.send_response(404); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with pytest.raises(RuntimeError, match="HTTP 404.*DeploymentNotFound"):
        llm._post(f"http://127.0.0.1:{srv.server_port}/x", {}, {})
    srv.shutdown()
