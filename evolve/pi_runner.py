"""Pi: the builder. Runs the approved ECP on its own git branch under enforced scope.

Two backends, same rules:
  - "pi"        : the real Pi coding agent (pi.dev), started with the guard extension and pointed at Azure OpenAI.
  - "scripted"  : plays back prepared edits (tests only). Every edit
                  goes through the SAME scope check, so the guard behaviour is visible
                  without any AI key.
Either way the control plane re-checks the final diff afterwards (see verify.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import Config, git
from .ecp import ECP, Evidence
from .scope import check_path, normalize

GUARD_TS = Path(__file__).resolve().parent.parent / "guard" / "guard.ts"


class PiResult:
    def __init__(self):
        self.applied: list[dict] = []  # edits that went through
        self.blocked: list[dict] = []  # edits the guard refused
        self.output = ""
        self.error = ""


def start_branch(repo: Path, ecp: ECP) -> str:
    branch = f"evolve/{ecp.id}"
    git(repo, "checkout", "-q", "-b", branch)
    return branch


def _tail_actions(repo: Path, path: Path, stop) -> None:
    """While real Pi runs, forward each guard decision (allowed/blocked) to the live feed as soon as it is written."""
    from .live import emit
    seen = 0
    while True:
        done = stop.is_set()
        try:
            lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            lines = []
        for l in lines[seen:]:
            try:
                r = json.loads(l)
                emit(repo, "pi_action", tool=r.get("tool"), path=r.get("path"), allowed=bool(r.get("allowed")), reason=r.get("reason", ""))
            except ValueError:
                pass
        seen = len(lines)
        if done:
            return
        stop.wait(0.4)


def _log(repo: Path, ecp: ECP, entry: dict) -> None:
    Evidence(repo, ecp.id).add("pi_action", entry)


def run_scripted(repo: Path, ecp: ECP, actions: list[dict]) -> PiResult:
    """actions: [{"tool": "write"|"edit", "path": ..., "content": ...} | {"tool":"edit","path":...,"old":...,"new":...}]"""
    res = PiResult()
    for a in actions:
        ok, why = check_path(a["path"], ecp.allowed_paths, ecp.forbidden_paths)
        rec = {"tool": a["tool"], "path": a["path"], "allowed": ok, "reason": why}
        _log(repo, ecp, rec)
        from .live import emit
        emit(repo, "pi_action", tool=a["tool"], path=a["path"], allowed=ok, reason=why)
        if not ok:
            res.blocked.append(rec)
            continue
        target = Path(repo) / normalize(a["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if a["tool"] == "write":
            target.write_text(a["content"], encoding="utf-8")
        elif a["tool"] == "edit":
            text = target.read_text(encoding="utf-8")
            if a["old"] not in text:
                res.error = f"edit failed: text not found in {a['path']}"
                continue
            target.write_text(text.replace(a["old"], a["new"], 1), encoding="utf-8")
        res.applied.append(rec)
    return res


def pi_prompt(ecp: ECP) -> str:
    return (
        "Implement the following approved plan. Edit ONLY the allowed paths. Do not touch anything else. "
        "Do not change the plan. When finished, stop.\n\n" + ecp.to_markdown()
    )


PROVIDER = "azure-evolve"


def pi_model(cfg: Config) -> str:
    """What `pi --model` gets: the Azure deployment through our own provider (unless config names a full provider/model)."""
    from . import azure
    m = cfg.pi.model or azure.settings().deployment
    return m if "/" in m else f"{PROVIDER}/{m}"


def write_pi_provider(run_dir: Path, cfg: Config) -> Path:
    """Point Pi at Azure: a private models.json in the run folder (it holds the key or a short-lived token, and is deleted after the run)."""
    from . import azure
    s = azure.settings()
    if s.missing():
        raise RuntimeError("Azure is not set up (missing " + ", ".join(s.missing()) + ").")
    d = Path(run_dir) / "pi-agent"
    d.mkdir(parents=True, exist_ok=True)
    dep = cfg.pi.model if cfg.pi.model and "/" not in cfg.pi.model else ""
    (d / "models.json").write_text(json.dumps(azure.pi_provider(s, dep)), encoding="utf-8")
    return d


def pi_args(cfg: Config, prompt: str, guard_path: str) -> list[str]:
    tools = "read,grep,find,ls,edit,write" + (",bash" if cfg.pi.allow_bash else "")
    a = ["-p", "--mode", "json", "--no-session", "--no-context-files", "-e", guard_path, "--tools", tools, "--model", pi_model(cfg)]
    return a + [prompt]


def podman_command(repo: Path, run_dir: Path, ecp: ECP, cfg: Config, prompt: str) -> list[str]:
    """The exact `podman run` line. Pi sees ONLY: the repo (read-write), the guard (read-only), a small run folder."""
    cmd = ["podman", "run", "--rm", "--userns=keep-id", "--security-opt", "no-new-privileges", "--cap-drop=ALL",
           "-v", f"{Path(repo).resolve()}:/workspace:Z",
           "-v", f"{GUARD_TS.parent}:/guard:ro,Z",
           "-v", f"{Path(run_dir).resolve()}:/ecp-run:Z",
           "-e", "ECP_SCOPE_FILE=/ecp-run/scope.json", "-e", "ECP_ACTION_LOG=/ecp-run/actions.jsonl", "-e", "PI_CODING_AGENT_DIR=/ecp-run/pi-agent"]
    return cmd + [cfg.pi.image] + pi_args(cfg, prompt, "/guard/guard.ts")


def pi_error(stdout: str) -> str:
    """Pi exits 0 even when the model call failed (bad key, unknown model, no credit). Read its event stream for the real reason."""
    last = ""
    for line in stdout.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        m = e.get("message") if isinstance(e, dict) else None
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("stopReason") == "error":
            last = str(m.get("errorMessage", "the model call failed"))[:400]
    return last


def run_pi(repo: Path, ecp: ECP, cfg: Config) -> PiResult:
    """Run the real Pi agent with the guard extension loaded (inside Podman if configured)."""
    res = PiResult()
    use_podman = cfg.pi.sandbox == "podman"
    exe = shutil.which("podman" if use_podman else "pi")
    if not exe:
        res.error = "podman is not installed." if use_podman else "Pi is not installed (npm i -g @earendil-works/pi-coding-agent)."
        return res
    work = Path(repo) / ".evolution" / "run"
    work.mkdir(parents=True, exist_ok=True)
    (work / "scope.json").write_text(json.dumps({"allowed": ecp.allowed_paths, "forbidden": ecp.forbidden_paths}), encoding="utf-8")
    action_log = work / "actions.jsonl"
    action_log.write_text("", encoding="utf-8")
    prompt = pi_prompt(ecp)
    try:
        agent_dir = write_pi_provider(work, cfg)
    except RuntimeError as e:
        res.error = str(e)
        return res
    if use_podman:
        cmd, env = podman_command(repo, work, ecp, cfg, prompt), None
        cmd[0] = exe
    else:
        cmd = [exe, *pi_args(cfg, prompt, str(GUARD_TS))]
        env = {**os.environ, "ECP_SCOPE_FILE": str(work / "scope.json"), "ECP_ACTION_LOG": str(action_log), "PI_CODING_AGENT_DIR": str(agent_dir)}
    import threading
    stop = threading.Event()
    watcher = threading.Thread(target=_tail_actions, args=(repo, action_log, stop), daemon=True)
    watcher.start()
    try:
        r = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=cfg.pi.timeout_s, stdin=subprocess.DEVNULL)  # Pi waits on an open stdin
        res.output = r.stdout[-4000:]
        if r.returncode != 0:
            res.error = (r.stderr or r.stdout)[-1000:] or f"exit code {r.returncode}"
        elif pi_error(r.stdout):
            res.error = "the model call failed: " + pi_error(r.stdout)
    except subprocess.TimeoutExpired:
        res.error = f"Pi timed out after {cfg.pi.timeout_s}s"
    stop.set()
    watcher.join(timeout=3)
    (agent_dir / "models.json").unlink(missing_ok=True)
    for line in action_log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            _log(repo, ecp, rec)
            (res.applied if rec.get("allowed") else res.blocked).append(rec)
    return res


def execute(repo: Path, ecp: ECP, cfg: Config, actions: list[dict] | None) -> PiResult:
    if cfg.pi.backend == "pi":
        return run_pi(repo, ecp, cfg)
    return run_scripted(repo, ecp, actions or [])
