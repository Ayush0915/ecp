"""The dashboard server. Read-only view of .evolution/ plus a few buttons (start a run, approve, compare, reset demo).

Standard library only. Listens on 127.0.0.1 by default (this machine only).
Everything shown is rebuilt from the repo's own files (ECP, versions, evidence log), so what you see on screen
is exactly what is on disk and can be opened and shown as plain files.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from ..ecp import ECP, Evidence, list_versions

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
SCENARIOS = ROOT / "demo" / "scenarios"


# ------------------------------------------------------------------ data (pure functions, tested)
def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def list_ecps(repo: Path) -> list[dict]:
    out = []
    for f in sorted((repo / ".evolution" / "ecps").glob("ECP-*.md")):
        try:
            e = ECP.load(f)
        except Exception:  # noqa: BLE001 - a half-written file during a live run
            continue
        entries = Evidence(repo, e.id).entries()
        dec = [x for x in entries if x["kind"] == "decision"]
        out.append({"id": e.id, "title": e.title, "change_type": e.change_type, "status": e.status, "risk": e.risk,
                    "outcome": dec[-1]["data"]["outcome"] if dec else "", "events": len(entries)})
    return out


def ecp_detail(repo: Path, ecp_id: str) -> dict:
    ev = Evidence(repo, ecp_id)
    versions = []
    for n, text in list_versions(repo, ecp_id):
        try:
            fields = ECP.from_markdown(text).model_dump(exclude={"body"})
            body = ECP.from_markdown(text).body
        except Exception:  # noqa: BLE001
            fields, body = {}, ""
        versions.append({"n": n, "text": text, "fields": fields, "body": body})
    p = repo / ".evolution" / "ecps" / f"{ecp_id}.md"
    return {"id": ecp_id, "file": _read(p), "versions": versions, "evidence": ev.entries(), "chain_intact": ev.chain_intact()}


def live_state(repo: Path, runner: "Runner | None") -> dict:
    pend = repo / ".evolution" / "run" / "pending_approval.json"
    return {"running": bool(runner and runner.running()), "log": runner.tail() if runner else "",
            "pending_approval": json.loads(pend.read_text()) if pend.exists() else None}


def overview(repo: Path, demo: bool) -> dict:
    cfgp = repo / ".evolution" / "config.yaml"
    cfg = yaml.safe_load(_read(cfgp)) or {} if cfgp.exists() else {}
    cmp_p = repo / ".evolution" / "compare" / "latest.json"
    hist = [json.loads(l) for l in _read(repo / ".evolution" / "history.jsonl").splitlines() if l.strip()]
    return {"repo": repo.name, "demo": demo, "ecps": list_ecps(repo), "history": hist,
            "config": {"provider": cfg.get("models", {}).get("provider"), "pi": cfg.get("pi", {}).get("backend"),
                       "sandbox": cfg.get("pi", {}).get("sandbox"), "tests": cfg.get("test_command")},
            "compare": json.loads(_read(cmp_p)) if cmp_p.exists() else None,
            "scenarios": sorted(p.stem for p in SCENARIOS.glob("*.json")) if demo else []}


FILES = {"config": ".evolution/config.yaml", "rules": ".evolution/rules.md", "history": ".evolution/history.jsonl"}


def read_file(repo: Path, name: str) -> str:
    if name in FILES:
        return _read(repo / FILES[name])
    if name.startswith("ecp:") and name[4:].replace("-", "").isalnum():
        return _read(repo / ".evolution" / "ecps" / f"{name[4:]}.md")
    return ""


# ------------------------------------------------------------------ background runs
class Runner:
    """Runs `evolve run` / `evolve compare` as a child process so the page can show it live."""

    def __init__(self, repo: Path):
        self.repo, self.proc, self.log_path = repo, None, repo / ".evolution" / "run" / "dashboard-run.log"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, kind: str, request: str, scenario: str | None) -> bool:
        if self.running():
            return False
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "evolve", kind, request, "--repo", str(self.repo), "--ask", "file"]
        if scenario:
            cmd += ["--scenario", str(SCENARIOS / f"{scenario}.json")]
        self.log_f = self.log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, stdout=self.log_f, stderr=subprocess.STDOUT, cwd=ROOT, env={**__import__("os").environ, "PYTHONUNBUFFERED": "1"})
        return True

    def tail(self, n: int = 6000) -> str:
        return _read(self.log_path)[-n:]


def make_demo_repo(dest: Path, scenario_for_config: str | None = None) -> Path:
    """A fresh copy of the demo inventory app, as a git repo set up for offline demos."""
    import shutil
    from ..config import git, init_repo
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(ROOT / "demo" / "inventory_app", dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    git(dest, "init", "-q", "-b", "main")
    git(dest, "add", "-A")
    git(dest, "-c", "user.name=Demo", "-c", "user.email=demo@localhost", "commit", "-q", "-m", "initial inventory app")
    init_repo(dest)
    p = dest / ".evolution" / "config.yaml"
    c = yaml.safe_load(p.read_text())
    c["test_command"] = f"{sys.executable} -m pytest -q"
    p.write_text(yaml.safe_dump(c, sort_keys=False))
    return dest


# ------------------------------------------------------------------ http
def make_handler(repo: Path, demo: bool, demo_dir: Path | None):
    runner = Runner(repo)
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.send_header("cache-control", "no-store"); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path == "/":
                    b = (HERE / "index.html").read_bytes()
                    self.send_response(200); self.send_header("content-type", "text/html; charset=utf-8"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
                elif u.path == "/api/overview":
                    self._json(overview(repo, demo))
                elif u.path == "/api/live":
                    self._json(live_state(repo, runner))
                elif u.path.startswith("/api/ecp/"):
                    self._json(ecp_detail(repo, u.path.rsplit("/", 1)[1]))
                elif u.path == "/api/file":
                    self._json({"text": read_file(repo, parse_qs(u.query).get("name", [""])[0])})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            u = urlparse(self.path)
            with lock:
                if u.path in ("/api/start", "/api/compare"):
                    kind = "run" if u.path == "/api/start" else "compare"
                    scen = body.get("scenario") if demo else None
                    req = (body.get("request") or "").strip()
                    if not req and not scen:
                        return self._json({"error": "type a request first"}, 400)
                    return self._json({"started": runner.start(kind, req, scen)})
                if u.path == "/api/approve":
                    run = repo / ".evolution" / "run"
                    run.mkdir(parents=True, exist_ok=True)
                    (run / "approval.json").write_text(json.dumps({"approve": bool(body.get("approve"))}))
                    return self._json({"ok": True})
                if u.path == "/api/reset" and demo and demo_dir is not None:
                    if runner.running():
                        return self._json({"error": "a run is in progress"}, 409)
                    make_demo_repo(demo_dir)
                    return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

    return H


def serve(repo: Path, host: str = "127.0.0.1", port: int = 8765, demo: bool = False, demo_dir: Path | None = None, open_browser: bool = True):
    srv = ThreadingHTTPServer((host, port), make_handler(repo, demo, demo_dir))
    url = f"http://{host}:{port}/"
    print(f"Dashboard for {repo}\n  open {url}   (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
