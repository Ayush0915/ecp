"""The live path with nothing mocked inside the app: real Azure client over HTTP, real Pi agent, real guard.
Only the AI's brain is faked (tests/fake_azure.py), so no credentials and no money are needed.
Needs the Pi agent installed (npm i -g @earendil-works/pi-coding-agent) and bandit/ruff/coverage; otherwise skipped."""
import importlib.util
import os
import shutil
from pathlib import Path

import pytest
import yaml

from evolve import discover as D
from evolve import engine
from evolve.config import load_config
from evolve.llm import make_llm
from tests.conftest import make_repo
from tests.fake_azure import FakeAzureServer

PI = os.environ.get("PI_BIN") or shutil.which("pi")
pytestmark = pytest.mark.skipif(not PI or not all(importlib.util.find_spec(m) for m in ("bandit", "ruff", "coverage")), reason="needs Pi and bandit, ruff, coverage")


@pytest.fixture
def live(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "shop", "shop_app", mock=False)
    fake = FakeAzureServer(repo, [
        {"tool": "edit", "args": {"path": "shop/pricing.py", "edits": [{"oldText": "import os\n", "newText": ""}]}},
        {"tool": "write", "args": {"path": "shop/auth.py", "content": "ADMIN_PASSWORD = 'changed'\n"}},   # outside the ECP: must be blocked
    ], ["shop/pricing.py"])
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "unused"))   # the app must point Pi at its own provider file
    monkeypatch.setenv("PATH", str(Path(PI).parent) + os.pathsep + os.environ["PATH"])
    for k, v in {"AZURE_OPENAI_BASE": fake.url, "AZURE_OPENAI_DEPLOYMENT": "gpt-test", "AZURE_OPENAI_API_VERSION": "2024-10-21", "AZURE_OPENAI_API_KEY": "test-key"}.items():
        monkeypatch.setenv(k, v)
    p = repo / ".evolution" / "config.yaml"
    c = yaml.safe_load(p.read_text())
    c["models"]["provider"] = "azure"
    c["pi"].update(backend="pi", sandbox="none", timeout_s=120)
    p.write_text(yaml.safe_dump(c, sort_keys=False))
    yield repo, fake
    fake.stop()


def test_discover_then_evolve_with_real_client_and_real_pi(live):
    repo, fake = live
    cfg = load_config(repo)
    llm = make_llm(cfg)
    s = D.discover(repo, llm, cfg)
    assert len(s["findings"]) == 1 and s["findings"][0]["evidence"] == 2 and s["findings"][0]["file"] == "shop/pricing.py"
    f = s["findings"][0]

    res = engine.run(repo, D.request_from_finding(f), llm, cfg, None, lambda q: True, finding=f)
    assert res.outcome == "accepted", res.reasons
    assert any("auth.py" in str(b) for b in res.blocked_edits)                                  # the guard stopped the out-of-scope write in real Pi
    assert "ADMIN_PASSWORD = \"hunter2-admin-pass\"" in (repo / "shop" / "auth.py").read_text()  # and the file is untouched
    assert "import os" not in (repo / "shop" / "pricing.py").read_text()
    assert D.load_finding(repo, f["id"])["status"] == "accepted"
    pi_calls = [c for c in fake.calls if c["kind"] == "pi"]
    assert pi_calls and all(c["model"] == "gpt-test" and c["path"].startswith("/openai/v1/") for c in pi_calls)   # Pi used the Azure route with the deployment as model
    assert any(c["kind"] == "llm" and c["path"].startswith("/openai/deployments/gpt-test/chat/completions") for c in fake.calls)
    assert not (repo / ".evolution" / "run" / "pi-agent" / "models.json").exists()


def test_wrong_key_is_reported_clearly(live, monkeypatch):
    repo, fake = live
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "wrong")
    with pytest.raises(RuntimeError, match="HTTP 401.*AZURE_OPENAI_API_KEY"):
        make_llm(load_config(repo)).complete("elephant:draft", "s", "u")


def test_unreachable_service_is_reported(live, monkeypatch):
    repo, fake = live
    fake.stop()
    from evolve import llm as L
    monkeypatch.setattr(L, "_sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="Could not reach the AI service"):
        make_llm(load_config(repo)).complete("elephant:draft", "s", "u")
