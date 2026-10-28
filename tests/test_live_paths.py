"""The 'live' code paths, exercised against local stand-ins (no internet, no keys):
  - a fake Azure OpenAI server (checks URL, api-version, key or bearer token; replays a scripted scenario)
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


AZURE_ENV = {"AZURE_OPENAI_BASE": "", "AZURE_OPENAI_DEPLOYMENT": "gpt-5.5", "AZURE_OPENAI_API_VERSION": "2024-10-21", "AZURE_OPENAI_API_KEY": "secret-key"}


class FakeAzure(BaseHTTPRequestHandler):
    """Azure OpenAI as the real service behaves: deployment in the URL, api-version in the query, key (or bearer token) in a header."""
    scenario_llm: MockLLM = None
    seen: list = []

    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers["content-length"])
        raw = self.rfile.read(n)
        if self.path.startswith("/tenant-1/oauth2/v2.0/token"):
            form = dict(p.split("=", 1) for p in raw.decode().split("&"))
            FakeAzure.seen.append({"path": self.path, "form": form})
            out = json.dumps({"access_token": "entra-token-123", "expires_in": 3600}).encode()
            self.send_response(200); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
            return
        body = json.loads(raw)
        FakeAzure.seen.append({"path": self.path, "key": self.headers.get("api-key"), "auth": self.headers.get("authorization")})
        system, user = body["messages"][0]["content"], body["messages"][1]["content"]
        if system.startswith("You are Elephant"):
            role = "elephant:revise" if "YOUR CURRENT ECP" in user else "elephant:draft"
        else:
            lens_dir = Path(__file__).resolve().parent.parent / "evolve" / "goldfish" / "lenses"
            role = "goldfish:" + next(n for n in LENS_NAMES if (lens_dir / f"{n}.md").read_text().strip() in system)
        text = FakeAzure.scenario_llm.complete(role, system, user)
        out = json.dumps({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}).encode()
        self.send_response(200); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)


@pytest.fixture
def azure_server(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), FakeAzure)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeAzure.seen = []
    base = f"http://127.0.0.1:{srv.server_port}"
    for k, v in {**AZURE_ENV, "AZURE_OPENAI_BASE": base}.items():
        monkeypatch.setenv(k, v)
    for k in ("AZURE_OPENAI_TENANTID", "AZURE_OPENAI_CLIENTID", "AZURE_OPENAI_CLIENTSECRET", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AZURE_AUTHORITY_HOST", base)
    yield base
    srv.shutdown()


def _azure_repo(demo_repo):
    p = demo_repo / ".evolution" / "config.yaml"
    c = yaml.safe_load(p.read_text()); c["models"]["provider"] = "azure"; p.write_text(yaml.safe_dump(c))
    return load_config(demo_repo)


def test_azure_end_to_end_with_api_key(demo_repo, azure_server):
    sc = scenario("1_flawed_plan_caught")
    FakeAzure.scenario_llm = MockLLM(sc)
    cfg = _azure_repo(demo_repo)
    res = engine.run(demo_repo, sc["request"], make_llm(cfg), cfg, sc["pi_actions"], lambda q: True)
    assert res.outcome == "accepted" and res.rounds == 2          # same story as the scripted run, over HTTP
    assert all(s["path"] == "/openai/deployments/gpt-5.5/chat/completions?api-version=2024-10-21" for s in FakeAzure.seen)
    assert all(s["key"] == "secret-key" and s["auth"] is None for s in FakeAzure.seen)


def test_azure_with_service_principal_gets_a_bearer_token_once(demo_repo, azure_server, monkeypatch):
    from evolve import azure
    azure._token_cache.update(key=None, expires=0)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY")
    for k, v in {"AZURE_OPENAI_TENANTID": "tenant-1", "AZURE_OPENAI_CLIENTID": "cid", "AZURE_OPENAI_CLIENTSECRET": "csecret"}.items():
        monkeypatch.setenv(k, v)
    sc = scenario("2_scope_violation_blocked")
    FakeAzure.scenario_llm = MockLLM(sc)
    cfg = _azure_repo(demo_repo)
    llm = make_llm(cfg)
    llm.complete("elephant:draft", "You are Elephant, the planner", "x")
    llm.complete("elephant:draft", "You are Elephant, the planner", "x")
    tokens = [s for s in FakeAzure.seen if "form" in s]
    assert len(tokens) == 1 and tokens[0]["form"]["client_id"] == "cid" and tokens[0]["form"]["grant_type"] == "client_credentials"
    assert [s["auth"] for s in FakeAzure.seen if "auth" in s] == ["Bearer entra-token-123"] * 2 and all(s["key"] is None for s in FakeAzure.seen if "auth" in s)


def test_azure_missing_settings_are_named(monkeypatch):
    for k in AZURE_ENV | {"AZURE_OPENAI_ENDPOINT": ""}:
        monkeypatch.delenv(k, raising=False)
    from evolve.config import Config
    cfg = Config(); cfg.models.provider = "azure"
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_BASE.*AZURE_OPENAI_DEPLOYMENT"):
        make_llm(cfg)


def test_azure_base_is_tidied(monkeypatch):
    from evolve import azure
    monkeypatch.setenv("AZURE_OPENAI_BASE", "https://res.openai.azure.com/openai/v1/")
    assert azure.settings().base == "https://res.openai.azure.com"
    assert azure.chat_url(azure.settings(), "d1").startswith("https://res.openai.azure.com/openai/deployments/d1/chat/completions?api-version=")


def test_pi_gets_an_azure_provider_file_and_it_is_removed_after_the_run(demo_repo, tmp_path, azure_server, monkeypatch):
    from evolve import azure, pi_runner
    cfg = load_config(demo_repo)
    d = pi_runner.write_pi_provider(tmp_path, cfg)
    prov = json.loads((d / "models.json").read_text())["providers"]["azure-evolve"]
    assert prov["baseUrl"].endswith("/openai/v1") and prov["models"][0]["id"] == "gpt-5.5" and prov["headers"] == {"api-key": "secret-key"}
    assert pi_runner.pi_model(cfg) == "azure-evolve/gpt-5.5"
    azure._token_cache.update(key=None, expires=0)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY")
    for k, v in {"AZURE_OPENAI_TENANTID": "tenant-1", "AZURE_OPENAI_CLIENTID": "cid", "AZURE_OPENAI_CLIENTSECRET": "cs"}.items():
        monkeypatch.setenv(k, v)
    prov = json.loads((pi_runner.write_pi_provider(tmp_path, cfg) / "models.json").read_text())["providers"]["azure-evolve"]
    assert prov["apiKey"] == "entra-token-123" and prov["authHeader"] is True


def test_podman_command_exposes_only_what_it_should(demo_repo, tmp_path):
    cfg = load_config(demo_repo); cfg.pi.sandbox, cfg.pi.model = "podman", "gpt-5.5"
    ecp = ECP(id="ECP-0001", base_commit="x", **scenario("2_scope_violation_blocked")["elephant"][0])
    cmd = podman_command(demo_repo, tmp_path, ecp, cfg, "PROMPT")
    joined = " ".join(cmd)
    assert cmd[:2] == ["podman", "run"] and "--cap-drop=ALL" in cmd and "no-new-privileges" in joined
    mounts = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
    assert len(mounts) == 3 and mounts[1].endswith("/guard:ro,Z")      # repo, guard (read-only), run folder. Nothing else.
    assert "--tools" in cmd and cmd[cmd.index("--tools") + 1] == "read,grep,find,ls,edit,write"   # no bash
    assert "--model" in cmd and "azure-evolve/gpt-5.5" in cmd and cmd[-1] == "PROMPT" and "PI_CODING_AGENT_DIR=/ecp-run/pi-agent" in cmd
    assert "secret" not in joined


def test_no_secret_is_written_into_the_podman_command(demo_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "super-secret-value")
    cfg = load_config(demo_repo); cfg.pi.model = "gpt-5.5"
    ecp = ECP(id="ECP-0001", base_commit="x", **scenario("2_scope_violation_blocked")["elephant"][0])
    assert "super-secret-value" not in " ".join(podman_command(demo_repo, tmp_path, ecp, cfg, "P"))


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


def test_real_pi_launch_plumbing_with_fake_pi(demo_repo, tmp_path, monkeypatch, azure_server):
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
    assert not (demo_repo / ".evolution" / "run" / "pi-agent" / "models.json").exists()   # the key file is gone


def test_missing_podman_gives_clear_error(demo_repo, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    cfg = load_config(demo_repo); cfg.pi.backend, cfg.pi.sandbox = "pi", "podman"
    ecp = ECP(id="ECP-0001", base_commit="x", **scenario("2_scope_violation_blocked")["elephant"][0])
    assert "podman is not installed" in run_pi(demo_repo, ecp, cfg).error


# ---------------------------------------------------------------- doctor
def test_doctor_reports_unset_repo(tmp_path):
    from evolve.cli import doctor_checks
    rows = doctor_checks(tmp_path)
    assert any((not ok) and "not set up" in name for ok, name, _ in rows)


def test_doctor_flags_missing_azure_env_and_failing_baseline(demo_repo, monkeypatch):
    from evolve.cli import doctor_checks
    for k in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_TENANTID"):
        monkeypatch.delenv(k, raising=False)
    cfg = _azure_repo(demo_repo)
    p = demo_repo / ".evolution" / "config.yaml"
    c = yaml.safe_load(p.read_text()); c["test_command"] = "false"; p.write_text(yaml.safe_dump(c))
    bad = " | ".join(name for ok, name, _ in doctor_checks(demo_repo) if not ok)
    assert "AZURE_OPENAI_BASE is set" in bad and "AZURE_OPENAI_DEPLOYMENT is set" in bad and "Azure credentials" in bad and "tests pass BEFORE any change (baseline)" in bad


def test_doctor_all_green_on_demo_repo(demo_repo):
    from evolve.cli import doctor_checks
    assert all(ok for ok, *_ in doctor_checks(demo_repo))


def test_doctor_catches_dirty_tree(demo_repo):
    from evolve.cli import doctor_checks
    (demo_repo / "inventory" / "store.py").write_text("# edited\n")
    assert any((not ok) and "clean" in name for ok, name, _ in doctor_checks(demo_repo, run_tests=False))


# ---------------------------------------------------------------- azure details
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
    with pytest.raises(RuntimeError, match="HTTP 404.*DeploymentNotFound.*AZURE_OPENAI_DEPLOYMENT"):
        llm._post(f"http://127.0.0.1:{srv.server_port}/x", {}, {})
    srv.shutdown()


def test_env_file_is_read_without_overriding(tmp_path, monkeypatch):
    from evolve.cli import load_env_files
    (tmp_path / ".env").write_text("# c\nexport FOO_KEY_X='abc'\nBAR_KEY_X=def\n", encoding="utf-8")
    monkeypatch.delenv("FOO_KEY_X", raising=False)
    monkeypatch.setenv("BAR_KEY_X", "kept")
    assert str(tmp_path / ".env") in load_env_files(str(tmp_path))
    import os
    assert os.environ["FOO_KEY_X"] == "abc" and os.environ["BAR_KEY_X"] == "kept"
    monkeypatch.delenv("FOO_KEY_X")
