"""The dashboard server: a view of .evolution/ plus a few buttons (connect a repo, discover, evolve, approve).

Standard library only. Listens on 127.0.0.1 by default (this machine only).
Everything shown is rebuilt from the repo's own files (ECP, versions, evidence log), so what you see on screen
is exactly what is on disk and can be opened and shown as plain files.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from ..config import config_path, governance_path, is_initialised, rules_path
from ..ecp import ECP, Evidence, list_versions

HERE = Path(__file__).parent
ROOT = HERE.parent.parent


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
    from ..decide import anchor_status
    return {"id": ecp_id, "file": _read(p), "versions": versions, "evidence": ev.entries(), "chain_intact": ev.chain_intact(),
            "anchor": anchor_status(repo, ecp_id)}


def live_state(repo: Path, runner: "Runner | None") -> dict:
    pend = repo / ".evolution" / "run" / "pending_approval.json"
    return {"running": bool(runner and runner.running()), "kind": getattr(runner, "kind", ""), "log": runner.tail() if runner else "",
            "pending_approval": json.loads(pend.read_text(encoding="utf-8")) if pend.exists() else None}


def findings_state(repo: Path) -> dict:
    from ..discover import list_findings
    base = repo / ".evolution" / "baseline.json"
    b = json.loads(_read(base)) if base.exists() else None
    return {"findings": list_findings(repo), "baseline": {k: b[k] for k in ("run", "time", "detectors", "signals", "proposed", "dropped")} if b else None}


def overview(repo: "Path | None") -> dict:
    if not repo:
        return {"connected": False}
    from ..repo import describe
    cfgp = config_path(repo)
    cfg = yaml.safe_load(_read(cfgp)) or {} if cfgp.exists() else {}
    from ..governance import GovernanceError, load_governance
    try:
        g = load_governance(repo)
        gov = {"level": g.autonomy_level, "describe": g.describe(), "error": ""}
    except GovernanceError as e:
        gov = {"level": None, "describe": "", "error": str(e)[:300]}
    gov["store"] = str(cfgp.parent)
    prep = repo / ".evolution" / "prepare.json"
    untrusted = repo / ".evolution" / "untrusted"
    return {"connected": True, "repo": describe(repo), "ecps": list_ecps(repo),
            "quarantined": sorted(f.name for f in untrusted.iterdir() if f.name != "README.txt") if untrusted.is_dir() else [], "config": {"provider": cfg.get("models", {}).get("provider"), "sandbox": cfg.get("pi", {}).get("sandbox"), "tests": cfg.get("test_command")},
            "governance": gov, **findings_state(repo), "prepare": json.loads(_read(prep)) if prep.exists() else None}


FILES = {"config": config_path, "rules": rules_path, "governance": governance_path,
         "history": lambda repo: repo / ".evolution" / "history.jsonl"}


def read_file(repo: Path, name: str) -> str:
    if name in FILES:
        return _read(FILES[name](repo))
    if name.startswith("ecp:") and name[4:].replace("-", "").isalnum():
        return _read(repo / ".evolution" / "ecps" / f"{name[4:]}.md")
    return ""


# ------------------------------------------------------------------ background runs
class Runner:
    """Runs `evolve run | discover | prepare` as a child process so the page can show it live."""

    def __init__(self, repo: Path):
        self.repo, self.proc, self.kind, self.log_path = repo, None, "", repo / ".evolution" / "run" / "dashboard-run.log"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, kind: str, request: str = "", finding: str | None = None, scenario: "Path | None" = None) -> bool:
        if self.running():
            return False
        from ..live import reset
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.kind = kind
        reset(self.repo, kind, request or finding or "")
        cmd = [sys.executable, "-m", "evolve", kind, "--repo", str(self.repo)] if kind in ("discover", "prepare") else \
              [sys.executable, "-m", "evolve", "run", request, "--repo", str(self.repo), "--ask", "file"] + (["--finding", finding] if finding else [])
        if scenario:  # tests only: a scripted AI instead of Azure
            cmd += ["--scenario", str(scenario)]
        self.log_f = self.log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, stdout=self.log_f, stderr=subprocess.STDOUT, cwd=ROOT, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        return True

    def tail(self, n: int = 6000) -> str:
        return _read(self.log_path)[-n:]


# ------------------------------------------------------------------ Azure connection (kept in the project's .env, never in a repo)
ENV_FILE = ROOT / ".env"
PLAIN = ["AZURE_OPENAI_BASE", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_TENANTID", "AZURE_OPENAI_CLIENTID"]
SECRET = ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_CLIENTSECRET"]


def get_azure() -> dict:
    from .. import azure
    s = azure.settings()
    return {"values": {k: os.environ.get(k, "") for k in PLAIN}, "secrets": {k: bool(os.environ.get(k)) for k in SECRET}, "auth": s.auth_mode, "missing": s.missing(), "env_file": str(ENV_FILE)}


def save_azure(body: dict) -> None:
    """Write the given variables into .env and this process. An empty value removes the variable (leave the key empty to use the service principal)."""
    clean = {}
    for k, v in body.items():
        if k not in PLAIN + SECRET:
            continue
        v = str(v if v is not None else "").strip()
        if "\n" in v or "\r" in v:
            raise ValueError(f"{k} must be one line.")
        if k == "AZURE_OPENAI_BASE" and v and not v.lower().startswith("https://"):
            raise ValueError("AZURE_OPENAI_BASE must start with https:// (like https://my-resource.openai.azure.com).")
        if k in SECRET and v == "":
            continue                      # the page never shows secrets, so an empty secret box means "unchanged"
        clean[k] = v
    for k in body.get("clear", []) if isinstance(body.get("clear"), list) else []:
        if k in SECRET:
            clean[k] = ""
    old = [l for l in (ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else [])]
    keep = [l for l in old if l.split("=", 1)[0].strip() not in clean]
    ENV_FILE.write_text("\n".join(keep + [f"{k}={v}" for k, v in clean.items() if v]) + "\n", encoding="utf-8")
    for k, v in clean.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def check_azure() -> dict:
    """One tiny real call, to prove the credentials and the deployment work."""
    from ..llm import AzureOpenAILLM
    try:
        out = AzureOpenAILLM().complete("check", "Reply with the single word: pong", "ping")
        return {"ok": True, "note": "Azure answered: " + out.strip()[:40]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "note": str(e)[:400]}


SETTINGS_FIELDS = {"elephant": ("models", "elephant"), "goldfish": ("models", "goldfish"), "correctness": ("models", "correctness"),
                   "test_command": (None, "test_command"), "sandbox": ("pi", "sandbox")}


def get_settings(repo: Path) -> dict:
    from ..governance import GovernanceError, load_governance
    cfg = yaml.safe_load(_read(config_path(repo))) or {}
    m = cfg.get("models", {})
    try:
        level = load_governance(repo).autonomy_level
    except GovernanceError:
        level = None
    return {"elephant": m.get("elephant", ""), "goldfish": m.get("goldfish", ""), "correctness": m.get("correctness", ""),
            "test_command": cfg.get("test_command", ""), "sandbox": cfg.get("pi", {}).get("sandbox", "none"), "autonomy_level": level,
            "store": str(config_path(repo).parent)}


def save_settings(repo: Path, body: dict) -> None:
    from ..governance import GovernanceError, save_governance
    if "autonomy_level" in body:
        try:
            save_governance(repo, {"autonomy_level": int(body["autonomy_level"])})
        except (GovernanceError, ValueError, TypeError) as e:
            raise ValueError(f"autonomy level must be 0, 1, 2 or 3 ({str(e)[:120]})") from None
    p = config_path(repo)
    cfg = yaml.safe_load(_read(p)) or {}
    for k, v in body.items():
        if k not in SETTINGS_FIELDS:
            continue
        v = str(v).strip()
        sect, name = SETTINGS_FIELDS[k]
        if k == "sandbox" and v not in ("none", "podman"):
            raise ValueError("sandbox must be none or podman")
        (cfg.setdefault(sect, {}) if sect else cfg)[name] = v
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


# ------------------------------------------------------------------ http
def make_handler(repo: "Path | None", auto_prepare: bool = True, workdir: Path | None = None, test_scenarios: Path | None = None):
    token = secrets.token_urlsafe(24)  # proves a POST came from the page this server served (stops other websites)
    st = {"repo": repo, "runner": Runner(repo) if repo else None,
          "ingest": {"state": "idle", "step": "", "percent": 0, "lines": [], "error": "", "spec": ""}}
    lock = threading.Lock()
    workdir = Path(workdir or ROOT / "workspaces")
    scen = lambda body: (Path(test_scenarios) / (body["scenario"] + ".json")) if test_scenarios and body.get("scenario") else None

    def ingest_job(spec: str):
        from ..config import init_repo
        from ..repo import RepoError, ingest
        ing = st["ingest"]
        log = lambda m: ing["lines"].append(m)
        def progress(step, pct):
            ing["step"], ing["percent"] = step, pct
        try:
            path, src, new = ingest(spec, workdir, log, progress)
            if not is_initialised(path):
                init_repo(path)
                new = True
            with lock:
                st["repo"], st["runner"] = path, Runner(path)
            ing.update(state="done", percent=100, step="ready")
            if new and auto_prepare:
                st["runner"].start("prepare")
        except RepoError as e:
            ing.update(state="error", error=str(e))
        except Exception as e:  # noqa: BLE001
            ing.update(state="error", error=f"Unexpected problem: {e}")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the terminal quiet
            pass

        def _allowed_hosts(self) -> set[str]:
            bind, port = self.server.server_address[:2]
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
            if bind not in ("", "0.0.0.0", "::"):
                hosts.add(f"{bind}:{port}")
            return hosts

        def _refuse(self, post: bool) -> str:
            """Why this request must be refused, or "" if it is fine. Defends against other websites (CSRF) and DNS rebinding."""
            if (self.headers.get("host") or "").lower() not in self._allowed_hosts():
                return "unexpected Host header"
            if not post:
                return ""
            origin = (self.headers.get("origin") or "").lower()
            if origin and origin.removeprefix("http://") not in self._allowed_hosts():
                return "cross-origin request refused"
            if not (self.headers.get("content-type") or "").lower().startswith("application/json"):
                return "content-type must be application/json"
            if not hmac.compare_digest(self.headers.get("x-evolve-token") or "", token):
                return "missing or wrong page token (reload the page)"
            return ""

        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.send_header("cache-control", "no-store"); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            repo, runner = st["repo"], st["runner"]
            q = parse_qs(u.query)
            why = self._refuse(post=False)
            if why:
                return self._json({"error": why}, 403)
            try:
                if u.path == "/":
                    b = (HERE / "index.html").read_text(encoding="utf-8").replace("__EVOLVE_TOKEN__", token).encode("utf-8")
                    self.send_response(200); self.send_header("content-type", "text/html; charset=utf-8"); self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)
                elif u.path == "/api/overview":
                    self._json(overview(repo))
                elif u.path == "/api/live":
                    self._json(live_state(repo, runner) if repo else {"running": False, "kind": "", "log": "", "pending_approval": None})
                elif u.path == "/api/events":
                    from ..live import read
                    self._json(read(repo, int((q.get("after") or ["0"])[0]), (q.get("ecp") or [""])[0]) if repo else [])
                elif u.path == "/api/ingest":
                    self._json({k: (v[-40:] if k == "lines" else v) for k, v in st["ingest"].items()})
                elif u.path == "/api/azure":
                    self._json(get_azure())
                elif u.path == "/api/doctor":
                    from ..cli import doctor_checks
                    self._json({"checks": [{"ok": ok, "text": t, "fix": h} for ok, t, h in doctor_checks(repo, False, False)] if repo else []})
                elif u.path == "/api/settings":
                    self._json(get_settings(repo) if repo else {})
                elif u.path.startswith("/api/ecp/"):
                    self._json(ecp_detail(repo, u.path.rsplit("/", 1)[1]))
                elif u.path == "/api/file":
                    self._json({"text": read_file(repo, (q.get("name") or [""])[0]) if repo else ""})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # noqa: BLE001
                self._json({"error": str(e)}, 500)

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n)
            why = self._refuse(post=True)
            if why:
                return self._json({"error": why}, 403)
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self._json({"error": "body must be JSON"}, 400)
            if not isinstance(body, dict):
                return self._json({"error": "body must be a JSON object"}, 400)
            u = urlparse(self.path)
            with lock:
                repo, runner = st["repo"], st["runner"]
            if u.path == "/api/connect":
                if (runner and runner.running()) or st["ingest"]["state"] == "working":
                    return self._json({"error": "something is already running; wait for it to finish"}, 409)
                spec = str(body.get("repo", "")).strip()
                try:
                    from ..repo import parse_source
                    parse_source(spec)                      # reject bad input at once, before any background work
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                st["ingest"].update(state="working", step="starting", percent=0, lines=[], error="", spec=spec)
                threading.Thread(target=ingest_job, args=(spec,), daemon=True).start()
                return self._json({"started": True})
            if u.path == "/api/azure":
                try:
                    save_azure(body)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                return self._json(get_azure())
            if u.path == "/api/azure/check":
                return self._json(check_azure())
            if not repo:
                return self._json({"error": "connect a repo first"}, 400)
            if u.path == "/api/prepare":
                return self._json({"started": runner.start("prepare")})
            if u.path == "/api/settings":
                try:
                    save_settings(repo, body)
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
                return self._json({"ok": True, **get_settings(repo)})
            if u.path == "/api/discover":
                return self._json({"started": runner.start("discover", scenario=scen(body))})
            if u.path == "/api/start":
                req = (body.get("request") or "").strip()
                fid = (body.get("finding") or "").strip() or None
                if fid and not (repo / ".evolution" / "findings" / f"{fid}.json").exists():
                    return self._json({"error": f"no finding {fid}"}, 400)
                if not req and not fid and not scen(body):
                    return self._json({"error": "type a request first"}, 400)
                return self._json({"started": runner.start("run", req, fid, scen(body))})
            if u.path == "/api/approve":
                run = repo / ".evolution" / "run"
                run.mkdir(parents=True, exist_ok=True)
                pend = run / "pending_approval.json"
                try:
                    pending = json.loads(pend.read_text(encoding="utf-8")) if pend.exists() else None
                except ValueError:
                    pending = None
                if not pending:
                    return self._json({"error": "nothing is waiting for a decision"}, 409)
                if pending.get("plan_hash") and body.get("plan_hash") != pending["plan_hash"]:
                    return self._json({"error": "this decision names a different version of the plan; reload the page and look again"}, 409)
                import getpass
                (run / "approval.json").write_text(json.dumps({"approve": bool(body.get("approve")), "ecp_id": pending.get("ecp_id", ""),
                                                               "plan_hash": pending.get("plan_hash", ""), "approver": getpass.getuser(),
                                                               "time": time.time()}), encoding="utf-8")
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

    H.state = st
    H.token = token
    return H


def serve(repo: "Path | None", host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True):
    srv = ThreadingHTTPServer((host, port), make_handler(repo))
    url = f"http://{host}:{port}/"
    print(f"Dashboard for {repo or '(paste a repository link in the page)'}\n  open {url}   (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
