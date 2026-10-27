"""Command line:  evolve init | run | compare | dashboard | doctor | sandbox | review | show | plan-diff | report | status | demo"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from . import engine, goldfish
from .config import init_repo, load_config, git
from .ecp import ECP, Evidence
from .llm import make_llm

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo"


def _ask_factory(yes: bool, mode: str = "terminal", repo: Path | None = None):
    """How to ask a human for a high-risk approval: in the terminal, or through the dashboard (a small file handshake)."""
    def ask(q: str) -> bool:
        if yes:
            print(f"  (auto-yes) {q}")
            return True
        if mode == "file":
            import time
            run = Path(repo) / ".evolution" / "run"
            run.mkdir(parents=True, exist_ok=True)
            ans = run / "approval.json"
            ans.unlink(missing_ok=True)
            (run / "pending_approval.json").write_text(json.dumps({"question": q, "time": time.time()}), encoding="utf-8")
            print(f"  Waiting for a human decision in the dashboard: {q}")
            try:
                for _ in range(60 * 60):  # up to an hour
                    if ans.exists():
                        return bool(json.loads(ans.read_text()).get("approve"))
                    time.sleep(1)
                return False
            finally:
                (run / "pending_approval.json").unlink(missing_ok=True)
                ans.unlink(missing_ok=True)
        return input(f"  {q} [y/N] ").strip().lower() == "y"
    return ask


def cmd_init(a):
    r = init_repo(Path(a.repo), a.profile or "")
    print("Initialised .evolution/ ->", ", ".join(r["created"]) or "already set up")
    print("Detected test command:", r["test_command"] or "(none; set test_command in .evolution/config.yaml)")


def _load_scenario(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def resolve_repo(spec: str, workdir: str = "workspaces") -> Path:
    """A local folder, or a git URL (cloned into ./workspaces/<name>)."""
    if spec.startswith(("http://", "https://", "git@")):
        name = spec.rstrip("/").split("/")[-1].removesuffix(".git")
        dest = Path(workdir) / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "-q", spec, str(dest)], check=True)
            print(f"Cloned {spec} -> {dest}")
        return dest.resolve()
    return Path(spec).resolve()


def failing_tests_request(repo: Path, cfg) -> str:
    """Trigger: if the tests are failing, turn the failure itself into the change request."""
    from .verify import run_cmd
    if not cfg.test_command:
        sys.exit("No test command is configured, so there are no tests to read failures from.")
    ok, out = run_cmd(repo, cfg.test_command)
    if ok:
        sys.exit("The tests pass; there is nothing to fix.")
    return "Automatic trigger: the test suite is failing. Find the root cause and fix it. Test output:\n\n" + out[-3000:]


def cmd_run(a):
    repo = resolve_repo(a.repo)
    if not (repo / ".evolution" / "config.yaml").exists():
        print("First time on this repo: setting up .evolution/ ...")
        init_repo(repo, a.profile or "")
    cfg = load_config(repo)
    scenario = _load_scenario(a.scenario)
    llm = make_llm(cfg, scenario)
    actions = scenario.get("pi_actions") if scenario else None
    request = a.request or (scenario or {}).get("request", "")
    if getattr(a, "from_file", None):
        request = (request + "\n\n" if request else "") + Path(a.from_file).read_text(encoding="utf-8")
    if getattr(a, "from_failing_tests", False):
        request = (request + "\n\n" if request else "") + failing_tests_request(repo, cfg)
    if not request:
        sys.exit("Give a change request, e.g.  evolve run \"Stock can go negative\"  (or --from-file issue.md, or --from-failing-tests)")
    res = engine.run(repo, request, llm, cfg, actions, _ask_factory(a.yes, getattr(a, "ask", "terminal"), repo), print)
    print(f"\nOutcome: {res.outcome.upper()}  ({res.ecp.id})")
    if a.open_report:
        import webbrowser
        from .report import write_report
        webbrowser.open(write_report(repo, res.ecp.id).as_uri())
    for r in res.reasons:
        print("  -", r)


def cmd_review(a):
    """Run only the Goldfish reviewers on a hand-written ECP (works with no Elephant and no Pi)."""
    repo = Path(a.repo).resolve()
    cfg = load_config(repo)
    ecp = ECP.load(Path(a.ecp))
    llm = make_llm(cfg, _load_scenario(a.scenario))
    fs = goldfish.review(llm, cfg, repo, ecp, a.phase)
    for f in fs:
        print(f"[{f.severity}] {f.reviewer} @ {f.location}: {f.claim}")
    print(f"{len(fs)} finding(s).")


def cmd_show(a):
    repo = Path(a.repo).resolve()
    ecp = ECP.load(repo / ".evolution" / "ecps" / f"{a.id}.md")
    print(ecp.to_markdown())
    ev = Evidence(repo, a.id)
    print("--- evidence trail (chain intact: %s) ---" % ev.chain_intact())
    for e in ev.entries():
        print(e["time"], e["kind"], json.dumps(e["data"], ensure_ascii=False)[:160])


def cmd_compare(a):
    """Run the same request without and with governance and print the difference."""
    from . import compare as cmp
    repo = resolve_repo(a.repo)
    if not (repo / ".evolution" / "config.yaml").exists():
        init_repo(repo, a.profile or "")
    cfg = load_config(repo)
    scenario = _load_scenario(a.scenario)
    llm = make_llm(cfg, scenario)
    request = a.request or (scenario or {}).get("request", "")
    if not request:
        sys.exit("Give a change request to compare.")
    pa = (scenario or {}).get("pi_actions")
    out = cmp.compare(repo, request, llm, cfg, (scenario or {}).get("plain_actions", pa), pa, _ask_factory(a.yes, getattr(a, "ask", "terminal"), repo), print)
    print("\n" + cmp.format_comparison(out))


def cmd_plan_diff(a):
    import difflib
    from .ecp import list_versions
    repo = Path(a.repo).resolve()
    vs = list_versions(repo, a.id)
    if len(vs) < 2:
        print(f"{a.id} has {len(vs)} version(s); the plan was never revised.")
        return
    for (n1, t1), (n2, t2) in zip(vs, vs[1:]):
        print(f"===== plan v{n1} -> v{n2} =====")
        print("".join(difflib.unified_diff(t1.splitlines(True), t2.splitlines(True), f"v{n1}", f"v{n2}", n=1)))


def cmd_report(a):
    from .report import write_report
    repo = Path(a.repo).resolve()
    ids = [a.id] if a.id else sorted(p.stem for p in (repo / ".evolution" / "ecps").glob("ECP-*.md"))
    for i in ids:
        print(write_report(repo, i))


def _have(cmd):
    return shutil.which(cmd) is not None


def doctor_checks(repo: Path, ping: bool = False, run_tests: bool = True) -> list[tuple[bool, str, str]]:
    """Every check returns (ok, what, how_to_fix). Pure function so it can be tested."""
    import os
    rows: list[tuple[bool, str, str]] = []
    add = lambda ok, name, hint="": rows.append((ok, name, hint))
    add(_have("git"), "git installed", "install git")
    add(sys.version_info >= (3, 10), f"python {sys.version_info.major}.{sys.version_info.minor} (need 3.10+)", "install Python 3.10 or newer")
    cfgp = repo / ".evolution" / "config.yaml"
    if not cfgp.exists():
        add(False, "repo not set up", "run: evolve init --profile azure-podman")
        return rows
    cfg = load_config(repo)
    add(True, f"repo set up: provider={cfg.models.provider}, pi={cfg.pi.backend}, sandbox={cfg.pi.sandbox}")
    is_git = (repo / ".git").exists()
    add(is_git, "repo is a git repo", "run git init and commit")
    if is_git:
        dirty = git(repo, "status", "--porcelain", "--", ".", ":(exclude).evolution", check=False)
        add(not dirty, "working tree is clean", "commit or stash your changes")
        add(bool(git(repo, "rev-parse", "HEAD", check=False)), "repo has at least one commit", "make an initial commit")
    add(bool(cfg.test_command), f"test command: {cfg.test_command or '(none)'}", "set test_command in .evolution/config.yaml; without tests nothing can be verified")
    if cfg.test_command and run_tests:
        from .verify import run_cmd
        ok, out = run_cmd(repo, cfg.test_command)
        add(ok, "tests pass BEFORE any change (baseline)", "fix the failing tests first: " + out.strip().splitlines()[-1][:120] if out.strip() else "fix the failing tests first")
    if cfg.models.provider == "azure":
        add(bool(os.environ.get(cfg.models.api_key_env or "AZURE_OPENAI_API_KEY")), "AZURE_OPENAI_API_KEY is set", "export AZURE_OPENAI_API_KEY=...")
        add(bool(os.environ.get(cfg.models.azure_endpoint_env) or os.environ.get("AZURE_OPENAI_BASE_URL") or os.environ.get("AZURE_OPENAI_RESOURCE_NAME")),
            f"{cfg.models.azure_endpoint_env} is set", "export AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com")
        add(bool(cfg.models.elephant), f"deployment name for planning/review: {cfg.models.elephant}", "set models.elephant to your Azure deployment name")
        second = cfg.models.correctness and cfg.models.correctness != cfg.models.goldfish
        add(True, "second model for the correctness reviewer: " + (cfg.models.correctness if second else "none (same deployment as the other reviewers: a weaker independence check)"))
    if cfg.pi.backend == "pi":
        if cfg.pi.sandbox == "podman":
            have = _have("podman")
            add(have, "podman installed", "sudo apt install podman")
            if have:
                info = subprocess.run(["podman", "info"], capture_output=True, text=True)
                add(info.returncode == 0, "podman works (podman info)", (info.stderr.strip().splitlines() or ["see podman docs"])[-1][:140])
                img = subprocess.run(["podman", "image", "exists", cfg.pi.image])
                add(img.returncode == 0, f"image {cfg.pi.image} built", "run: evolve sandbox build")
                if img.returncode == 0:
                    v = subprocess.run(["podman", "run", "--rm", cfg.pi.image, "--version"], capture_output=True, text=True, timeout=120)
                    add(v.returncode == 0, f"Pi runs inside the container ({v.stdout.strip()[:20]})", "rebuild: evolve sandbox build")
        else:
            add(_have("pi"), "pi installed", "npm i -g @earendil-works/pi-coding-agent")
    if ping and cfg.models.provider != "mock":
        try:
            out = make_llm(cfg).complete("doctor", "Reply with the single word: pong", "ping", cfg.models.elephant)
            add("pong" in out.lower(), f"model answered: {out.strip()[:40]!r}", "check the deployment name and endpoint")
        except Exception as e:  # noqa: BLE001
            add(False, "model call failed", str(e)[:200])
    return rows


def cmd_doctor(a):
    """Is this machine ready for a live run? Prints what works and what is missing."""
    rows = doctor_checks(Path(a.repo).resolve(), a.ping, not a.skip_tests)
    bad = 0
    for ok, name, hint in rows:
        print(("  OK    " if ok else "  FIX   ") + name + ("" if ok or not hint else f"   -> {hint}"))
        bad += not ok
    print("\nReady." if not bad else f"\n{bad} thing(s) to fix.")
    sys.exit(1 if bad else 0)


def cmd_dashboard(a):
    """Open the live dashboard for a repo (or --demo for a practice repo with scripted scenarios)."""
    from .dashboard.server import make_demo_repo, serve
    if a.demo:
        dest = Path(a.dest) if a.dest else Path(tempfile.mkdtemp(prefix="evolve-dashboard-demo-")) / "inventory"
        repo = make_demo_repo(dest)
        print(f"Practice repo: {repo}  (use the 'Reset demo repo' button to start over)")
        serve(repo, a.host, a.port, demo=True, demo_dir=dest, open_browser=not a.no_open)
    else:
        repo = resolve_repo(a.repo)
        serve(repo, a.host, a.port, demo=False, open_browser=not a.no_open)


def cmd_sandbox(a):
    ctx = Path(__file__).resolve().parent.parent / "sandbox"
    img = a.image
    print(f"Building {img} (Pi inside a container)...")
    subprocess.run(["podman", "build", "-t", img, "-f", str(ctx / "Dockerfile.pi"), str(ctx)], check=True)


def cmd_status(a):
    repo = Path(a.repo).resolve()
    h = repo / ".evolution" / "history.jsonl"
    if not h.exists():
        print("No evolutions yet.")
        return
    for l in h.read_text().splitlines():
        d = json.loads(l)
        print(f"{d['id']}  {d['outcome']:<11} {d['change_type']:<11} {d['title']}")


def cmd_demo(a):
    scen_dir = DEMO_DIR / "scenarios"
    names = sorted(p.stem for p in scen_dir.glob("*.json"))
    if a.name == "list":
        print("\n".join(names)); return
    chosen = names if a.name == "all" else [a.name]
    dest = Path(a.dest) if a.dest else Path(tempfile.mkdtemp(prefix="evolve-demo-"))
    for n in chosen:
        repo = dest / n if len(chosen) > 1 or a.dest else dest
        if repo.exists():
            shutil.rmtree(repo)
        shutil.copytree(DEMO_DIR / "inventory_app", repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        for cmd in (["init", "-q", "-b", "main"], ["add", "-A"]):
            git(repo, *cmd)
        git(repo, "-c", "user.name=Demo", "-c", "user.email=demo@localhost", "commit", "-q", "-m", "initial inventory app")
        init_repo(repo)
        cfg_p = repo / ".evolution" / "config.yaml"
        c = yaml.safe_load(cfg_p.read_text()); c["test_command"] = f"{sys.executable} -m pytest -q"; cfg_p.write_text(yaml.safe_dump(c, sort_keys=False))
        print(f"\n===== DEMO: {n}  (repo: {repo}) =====")
        class A: pass
        b = A(); b.repo, b.scenario, b.request, b.yes, b.profile, b.open_report = str(repo), str(scen_dir / f"{n}.json"), None, True, "", False
        cmd_run(b)
    print("\nDemo repos are in:", dest)


def main(argv=None):
    p = argparse.ArgumentParser(prog="evolve", description="ECP-governed evolution")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--repo", default="."); s.add_argument("--profile", choices=["azure-podman"], help="preset: Azure OpenAI + Pi in Podman"); s.set_defaults(f=cmd_init)
    s = sub.add_parser("run"); s.add_argument("request", nargs="?"); s.add_argument("--repo", default=".", help="folder or git URL"); s.add_argument("--scenario"); s.add_argument("--profile", choices=["azure-podman"]); s.add_argument("--yes", action="store_true", help="auto-approve high-risk gates"); s.add_argument("--open-report", action="store_true", help="open the HTML report when done"); s.add_argument("--from-file", help="use the text of a file (e.g. an issue) as the request"); s.add_argument("--from-failing-tests", action="store_true", help="trigger: use the failing test output as the request"); s.add_argument("--ask", choices=["terminal", "file"], default="terminal", help="where high-risk approvals are asked (file = via the dashboard)"); s.set_defaults(f=cmd_run)
    s = sub.add_parser("compare"); s.add_argument("request", nargs="?"); s.add_argument("--repo", default="."); s.add_argument("--scenario"); s.add_argument("--profile", choices=["azure-podman"]); s.add_argument("--yes", action="store_true"); s.add_argument("--ask", choices=["terminal", "file"], default="terminal"); s.set_defaults(f=cmd_compare)
    s = sub.add_parser("plan-diff"); s.add_argument("id"); s.add_argument("--repo", default="."); s.set_defaults(f=cmd_plan_diff)
    s = sub.add_parser("report"); s.add_argument("id", nargs="?"); s.add_argument("--repo", default="."); s.set_defaults(f=cmd_report)
    s = sub.add_parser("doctor"); s.add_argument("--repo", default="."); s.add_argument("--ping", action="store_true", help="also make one real model call"); s.add_argument("--skip-tests", action="store_true", help="do not run the baseline tests"); s.set_defaults(f=cmd_doctor)
    s = sub.add_parser("dashboard"); s.add_argument("--repo", default="."); s.add_argument("--port", type=int, default=8765); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--demo", action="store_true", help="practice mode: a demo repo with scripted scenarios, no AI key needed"); s.add_argument("--dest", help="where to create the practice repo"); s.add_argument("--no-open", action="store_true"); s.set_defaults(f=cmd_dashboard)
    s = sub.add_parser("sandbox"); s.add_argument("action", choices=["build"]); s.add_argument("--image", default="evolve-pi:latest"); s.set_defaults(f=cmd_sandbox)
    s = sub.add_parser("review"); s.add_argument("ecp"); s.add_argument("--repo", default="."); s.add_argument("--phase", default="before", choices=["before", "after"]); s.add_argument("--scenario"); s.set_defaults(f=cmd_review)
    s = sub.add_parser("show"); s.add_argument("id"); s.add_argument("--repo", default="."); s.set_defaults(f=cmd_show)
    s = sub.add_parser("status"); s.add_argument("--repo", default="."); s.set_defaults(f=cmd_status)
    s = sub.add_parser("demo"); s.add_argument("name", nargs="?", default="list", help="list | all | <scenario name>"); s.add_argument("--dest"); s.set_defaults(f=cmd_demo)
    a = p.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
