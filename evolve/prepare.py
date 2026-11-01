"""Get a freshly cloned repo ready to be tested: its own environment, its dependencies, a working test command.

Why: the repo's tests need the repo's packages. Without this, a new repo looks "broken" only because nothing is installed.
Python repos get a private virtual environment OUTSIDE the repo (so git stays clean); Node repos get `npm install`.
Everything is best effort and logged; a failed install is reported, never hidden.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

import yaml

from .config import Config, config_path, load_config
from .live import emit
from .verify import run_cmd

ROOT = Path(__file__).resolve().parent.parent


def env_dir(repo: Path) -> Path:
    return ROOT / "workspaces" / ".envs" / Path(repo).resolve().name


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def is_python_repo(repo: Path) -> bool:
    repo = Path(repo)
    return any((repo / f).exists() for f in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pytest.ini")) or any(repo.glob("*.py")) or (repo / "tests").is_dir()


def _run(cmd: list[str], cwd: Path, timeout: int, log: Callable[[str], None]) -> bool:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        log(f"  timed out: {' '.join(cmd[:4])}...")
        return False
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        log("  failed: " + " | ".join(tail)[:300])
    return r.returncode == 0


def requirement_lines(path: Path) -> list[str]:
    """The installable lines of a requirements file (no comments, no pip options, no nested -r)."""
    out = []
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.split(" #")[0].strip()
        if line and not line.startswith(("#", "-")):
            out.append(line)
    return out


def install_requirements_file(py: str, repo: Path, name: str, log: Callable[[str], None]) -> dict:
    """Install a requirements file. If pip refuses the whole file because ONE package will not build
    (common: pinned old versions on a new Python), install the lines one by one and report the ones that failed."""
    if _run([py, "-m", "pip", "install", "-q", "-r", name], repo, 900, lambda m: None):
        return {"file": name, "ok": True, "failed": []}
    log(f"  {name}: one package refused to install; trying the others one by one")
    failed = []
    for req in requirement_lines(repo / name):
        if not _run([py, "-m", "pip", "install", "-q", req], repo, 600, lambda m: None):
            failed.append(req)
    if failed:
        log("  could not install: " + ", ".join(failed))
    return {"file": name, "ok": not failed, "failed": failed}


def install_commands(repo: Path, py: str) -> list[list[str]]:
    """The pip commands that fit this repo's layout (only ones whose files exist)."""
    repo = Path(repo)
    cmds = []
    for name in ("requirements.txt", "requirements-dev.txt", "requirements_dev.txt", "requirements-test.txt", "dev-requirements.txt"):
        if (repo / name).exists():
            cmds.append([py, "-m", "pip", "install", "-q", "-r", name])
    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        cmds.append([py, "-m", "pip", "install", "-q", "-e", "."])
    return cmds


def prepare_environment(repo: Path, log: Callable[[str], None] = print) -> dict:
    repo = Path(repo)
    cfg = load_config(repo)
    out = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": "unknown", "steps": [], "baseline": None}
    if is_python_repo(repo):
        out["kind"] = "python"
        emit(repo, "prep_start", kind="python")
        venv = env_dir(repo)
        py = venv_python(venv)
        if not py.exists():
            log(f"Creating a private Python environment for this repo (outside the repo): {venv}")
            emit(repo, "prep_step", name="environment", state="running", note="creating a private Python environment outside the repo")
            venv.parent.mkdir(parents=True, exist_ok=True)
            if not _run([sys.executable, "-m", "venv", str(venv)], repo, 300, log):
                out["steps"].append("venv failed")
                return _finish(repo, cfg, out, log)
        emit(repo, "prep_step", name="environment", state="ok", note=str(venv))
        emit(repo, "prep_step", name="dependencies", state="running", note="installing the repo's requirements (a few minutes)")
        log("Installing the repo's dependencies (this can take a few minutes)...")
        _run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], repo, 300, log)
        for c in install_commands(repo, str(py)):
            log("  " + " ".join(c[3:]))
            if c[3:6] == ["install", "-q", "-r"]:
                out["steps"].append(install_requirements_file(str(py), repo, c[6], log))
            else:
                ok = _run(c, repo, 900, log)
                out["steps"].append({"cmd": " ".join(c[3:]), "ok": ok})
        emit(repo, "prep_step", name="dependencies", state="ok", note=", ".join(x.get("file") or x.get("cmd", "") for x in out["steps"] if isinstance(x, dict)) or "no requirements files")
        log("Installing the test tools (pytest, coverage)...")
        _run([str(py), "-m", "pip", "install", "-q", "pytest", "coverage"], repo, 600, log)
        raw = yaml.safe_load(config_path(repo).read_text(encoding="utf-8")) or {}
        raw["python"] = str(py)
        if not raw.get("test_command") or raw["test_command"].startswith("python -m pytest"):
            raw["test_command"] = f'"{py}" -m pytest -q'
        config_path(repo).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        cfg = load_config(repo)
    elif (repo / "package.json").exists():
        out["kind"] = "node"
        emit(repo, "prep_start", kind="node")
        emit(repo, "prep_step", name="dependencies", state="running", note="npm install")
        if not (repo / "node_modules").exists():
            log("Running npm install...")
            ok = _run(["npm", "install", "--no-audit", "--no-fund"], repo, 900, log)
            out["steps"].append({"cmd": "npm install", "ok": ok})
        emit(repo, "prep_step", name="dependencies", state="ok", note="npm install")
    else:
        log("Not a Python or Node repo: nothing to install. Discovery will rely on the repo's own tests and on reading the code.")
    return _finish(repo, cfg, out, log)


MISSING = re.compile(r"No module named ['\"]?([A-Za-z0-9_\.\-]+)")
PIP_NAMES = {"yaml": "pyyaml", "PIL": "pillow", "cv2": "opencv-python", "sklearn": "scikit-learn", "bs4": "beautifulsoup4", "dateutil": "python-dateutil", "dotenv": "python-dotenv"}


def canon(name: str) -> str:
    """Package names compare case-insensitively with -, _ and . treated alike (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def declared_packages(repo: Path) -> set[str]:
    """Every package the repository itself names as a dependency (requirements*.txt, pyproject.toml, setup.cfg, setup.py)."""
    repo = Path(repo)
    names: set[str] = set()
    for f in sorted(repo.glob("*requirements*.txt")) + sorted(repo.glob("requirements/*.txt")):
        for line in requirement_lines(f):
            m = REQ_NAME.match(line)
            if m:
                names.add(canon(m.group(1)))
    for f in (repo / "pyproject.toml", repo / "setup.cfg", repo / "setup.py"):
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        # dependency lists: dependencies = [...], optional groups, install_requires/tests_require (= [...] or indented lines)
        for block in re.findall(r"(?:dependencies|requires|install_requires|tests_require|extras_require|\w+)\s*=\s*\[(.*?)\]", text, re.S):
            for item in re.findall(r"['\"]\s*([A-Za-z0-9][A-Za-z0-9._-]*)", block):
                names.add(canon(item))
        for block in re.findall(r"(?:install_requires|tests_require)\s*=\s*\n((?:[ \t]+\S.*\n?)+)", text):
            for line in block.splitlines():
                m = REQ_NAME.match(line)
                if m:
                    names.add(canon(m.group(1)))
    return names


def failure_detail(text: str, limit: int = 900) -> str:
    """The lines that explain a failing test run (not just its last line, which is only a count)."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    key = [l for l in lines if re.match(r"^(E\s|ERROR|FAILED|.*Error:)", l)]
    return "\n".join((key or lines)[:14])[:limit]


def _finish(repo: Path, cfg: Config, out: dict, log: Callable[[str], None]) -> dict:
    if cfg.test_command:
        log(f"Running the repo's tests to get a baseline: {cfg.test_command}")
        emit(repo, "prep_step", name="baseline", state="running", note=cfg.test_command)
        ok, text = run_cmd(repo, cfg.test_command, 600)
        py = cfg.python
        declared = declared_packages(repo)
        tried: set[str] = set()
        for _ in range(6):  # a missing package the REPO DECLARES is installed, then the tests run again
            m = MISSING.search(text) if not ok else None
            if not (m and py):
                break
            mod = m.group(1).split(".")[0]
            pkg = PIP_NAMES.get(mod, mod)
            if canon(pkg) not in declared and canon(mod) not in declared:
                # the name comes from the output of untrusted code: installing it blindly would let a repository pick what we install
                log(f"  the tests need '{mod}', but the repository does not declare it as a dependency; not installing it. "
                    "Add it to the repository's requirements (or install it yourself) if it is genuine.")
                out.setdefault("not_installed", []).append(mod)
                break
            if pkg in tried:
                break
            tried.add(pkg)
            log(f"  the tests need '{pkg}' (declared by the repository); installing it")
            if not _run([py, "-m", "pip", "install", "-q", pkg], repo, 600, log):
                break
            ok, text = run_cmd(repo, cfg.test_command, 600)
        tail = [l for l in text.strip().splitlines() if l.strip()][-1:] or [""]
        out["baseline"] = {"ok": ok, "tail": tail[0][:200], "detail": "" if ok else failure_detail(text)}
        log(("  baseline PASSES: " if ok else "  baseline FAILS (failing tests become findings): ") + tail[0][:160])
        emit(repo, "prep_step", name="baseline", state="ok" if ok else "fail", note=tail[0][:160])
        if not ok:
            for l in out["baseline"]["detail"].splitlines()[:8]:
                log("    " + l[:200])
    else:
        log("No test command could be detected. Set one in Settings; without tests nothing can be verified.")
    emit(repo, "prep_done", baseline=out["baseline"])
    p = Path(repo) / ".evolution" / "prepare.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out
