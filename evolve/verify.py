"""Verification done by plain programs, not AI: run the tests, check the diff stayed in scope."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from .config import Config, git
from .ecp import ECP
from .live import emit
from .scope import check_changed_files


TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]*$|_test\.[A-Za-z]+$|\.(test|spec)\.[A-Za-z]+$")


def is_test_file(path: str) -> bool:
    return bool(TEST_FILE.search(path))


class VerifyResult:
    def __init__(self):
        self.fail_before = {"status": "skipped", "note": "not required for this change type"}
        self.test_ok = True
        self.test_output = ""
        self.check_results: list[dict] = []
        self.scope_problems: list[str] = []
        self.changed_files: list[str] = []
        self.diff = ""

    @property
    def ok(self) -> bool:
        return self.test_ok and not self.scope_problems and all(c["ok"] for c in self.check_results) and self.fail_before["status"] in ("proved", "skipped")

    def summary(self) -> str:
        bits = ["tests " + ("passed" if self.test_ok else "FAILED")]
        bits.append("scope clean" if not self.scope_problems else f"scope violations: {self.scope_problems}")
        for c in self.check_results:
            bits.append(f"check '{c['description']}': {'ok' if c['ok'] else 'FAILED'}")
        if self.fail_before["status"] != "skipped":
            bits.append("fail-before proof: " + self.fail_before["status"].replace("_", " "))
        return "; ".join(bits)


def run_cmd(repo: Path, cmd: str, timeout: int = 300) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, shell=True, cwd=repo, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    return r.returncode == 0, (r.stdout + r.stderr)[-3000:]


SHELL_CHARS = re.compile(r"[;&|<>`$\r\n]")
CHECK_RUNNERS = "pytest, python -m pytest, npm/pnpm/yarn test, go test, cargo test"


def check_argv(cmd: str, cfg: Config) -> tuple[list[str] | None, str]:
    """An ECP check command is written by the AI, so it is NOT run through a shell. It must be a known test runner
    (or start with one of cfg.allowed_check_prefixes). Returns (argv, "") or (None, why_refused)."""
    if SHELL_CHARS.search(cmd):
        return None, "shell operators (; & | < > ` $) are not allowed in a check command"
    try:
        toks = [t.strip("\"'") for t in shlex.split(cmd, posix=os.name != "nt")]
    except ValueError as e:
        return None, f"could not read the command ({e})"
    if not toks:
        return None, "empty command"
    exe = re.sub(r"\.(exe|cmd|bat)$", "", Path(toks[0]).name.lower())
    rest = toks[1:]
    py = cfg.python or None
    ok = (exe == "pytest" or (exe.startswith("python") and rest[:2] == ["-m", "pytest"])
          or (exe in ("npm", "pnpm", "yarn", "go", "cargo") and rest[:1] == ["test"])
          or any(cmd.strip() == p or cmd.strip().startswith(p + " ") for p in cfg.allowed_check_prefixes))
    if not ok:
        return None, f"'{toks[0]}' is not an allowed check command (allowed: {CHECK_RUNNERS})"
    if exe == "pytest" and py:
        toks = [py, "-m", "pytest", *rest]  # the repo's own environment
    elif exe in ("python", "python3") and py:
        toks = [py, *rest]
    toks[0] = shutil.which(toks[0]) or toks[0]  # npm is npm.cmd on Windows
    return toks, ""


def run_check(repo: Path, cmd: str, cfg: Config, timeout: int = 300) -> tuple[bool, str]:
    argv, why = check_argv(cmd, cfg)
    if argv is None:
        return False, "command not allowed: " + why
    try:
        r = subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except OSError as e:
        return False, f"could not start: {e}"
    return r.returncode == 0, (r.stdout + r.stderr)[-3000:]


def changed_files(repo: Path, base_commit: str) -> list[str]:
    """Everything that differs from the base commit, including brand-new files."""
    git(repo, "add", "-A", "--", ".", ":(exclude).evolution")
    out = git(repo, "diff", "--cached", "--name-only", base_commit)
    return [l for l in out.splitlines() if l.strip()]


def verify(repo: Path, ecp: ECP, cfg: Config) -> VerifyResult:
    r = VerifyResult()
    r.changed_files = changed_files(repo, ecp.base_commit)
    r.diff = git(repo, "diff", "--cached", ecp.base_commit) if r.changed_files else ""
    r.scope_problems = check_changed_files(r.changed_files, ecp.allowed_paths, ecp.forbidden_paths)
    emit(repo, "verify_step", name="scope", state="fail" if r.scope_problems else "ok", note="; ".join(r.scope_problems) or f"{len(r.changed_files)} file(s) changed, all inside the plan")
    emit(repo, "verify_step", name="tests", state="running", note=cfg.test_command or "no test command")
    if cfg.test_command:
        r.test_ok, r.test_output = run_cmd(repo, cfg.test_command)
    else:
        r.test_ok, r.test_output = False, "No test command configured; cannot verify. Set the test command in Settings."
    emit(repo, "verify_step", name="tests", state="ok" if r.test_ok else "fail", note=(r.test_output.strip().splitlines() or [""])[-1][:160])
    if ecp.change_type in cfg.require_fail_before and r.test_ok:
        emit(repo, "verify_step", name="fail_before", state="running", note="putting the old code back to see whether the new tests fail on it")
        r.fail_before = fail_before_check(repo, ecp, cfg, r.changed_files)
        emit(repo, "verify_step", name="fail_before", state="ok" if r.fail_before["status"] == "proved" else "fail", note=r.fail_before.get("note", ""))
    for chk in ecp.verification:
        if chk.command:
            ok, out = run_check(repo, chk.command, cfg)
            r.check_results.append({"description": chk.description, "command": chk.command, "ok": ok, "output": out[-500:]})
    return r


def _junit_outcomes(xml_path: Path) -> dict[str, str] | None:
    """test id -> passed | failure | error | skipped, read from a pytest --junitxml report. None if there is no report.
    'failure' = the test ran and an assertion (or the code under test) failed; 'error' = it could not even run
    (collection/import error, broken fixture)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError):
        return None
    out: dict[str, str] = {}
    for tc in root.iter("testcase"):
        tid = f"{tc.get('classname', '')}::{tc.get('name', '')}"
        tags = {c.tag for c in tc}
        out[tid] = "error" if "error" in tags else "failure" if "failure" in tags else "skipped" if "skipped" in tags else "passed"
    return out


def _run_targeted(repo: Path, cfg: Config, tests: list[str], xml_path: Path) -> tuple[dict[str, str] | None, str]:
    """Run ONLY the given test files with the repo's pytest command, and read the per-test outcomes."""
    xml_path.unlink(missing_ok=True)
    files = " ".join(f'"{t}"' for t in tests)
    _, out = run_cmd(repo, f'{cfg.test_command} {files} -p no:cacheprovider --junitxml="{xml_path}"')
    return _junit_outcomes(xml_path), out


def _put_old_sources_back(repo: Path, base_commit: str, sources: list[str]) -> dict[str, bytes | None]:
    saved: dict[str, bytes | None] = {}
    for f in sources:
        path = repo / f
        saved[f] = path.read_bytes() if path.exists() else None
        old = subprocess.run(["git", "-C", str(repo), "show", f"{base_commit}:{f}"], capture_output=True)
        if old.returncode == 0:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(old.stdout)  # put the old version back
        elif path.exists():
            path.unlink()  # file did not exist before the change
    return saved


def _restore(repo: Path, saved: dict[str, bytes | None]) -> None:
    for f, content in saved.items():
        path = repo / f
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def fail_before_check(repo: Path, ecp: ECP, cfg: Config, changed: list[str]) -> dict:
    """Prove the new tests are real: put the OLD source code back (keeping the new tests) and run the tests.
    The working tree is always restored afterwards. No AI involved.

    With pytest the proof is precise: only the new/changed test files are run, on the new code and then on the old code,
    and the change is PROVED only if at least one test FAILS (an assertion, not an import or collection error) on the old
    code AND PASSES on the new code. A test that was already failing, or one that cannot even be collected on the old code
    (because it imports something the fix added), proves nothing about the bug.
    Other test runners fall back to running the whole test command (coarser)."""
    import tempfile
    repo = Path(repo)
    tests = [f for f in changed if is_test_file(f) and (repo / f).exists()]
    if not tests:
        return {"status": "no_tests", "note": "this change type must add or change a test, and none was added"}
    if not cfg.test_command:
        return {"status": "not_proved", "note": "no test command configured"}
    sources = [f for f in changed if not is_test_file(f)]
    py_tests = [t for t in tests if t.endswith(".py")]
    if "pytest" in cfg.test_command and py_tests:
        xml = Path(tempfile.mkdtemp(prefix="evolve-fb-")) / "report.xml"
        new, _ = _run_targeted(repo, cfg, py_tests, xml)
        saved = {}
        try:
            saved = _put_old_sources_back(repo, ecp.base_commit, sources)
            old, old_out = _run_targeted(repo, cfg, py_tests, xml)
        finally:
            _restore(repo, saved)
        if new is None or old is None:
            return {"status": "not_proved", "note": "pytest wrote no report for the new tests, so nothing could be proved", "tests": py_tests}
        proving = sorted(t for t, o in old.items() if o == "failure" and new.get(t) == "passed")
        if proving:
            return {"status": "proved", "note": f"{len(proving)} test(s) fail on the old code and pass on the new code", "tests": proving,
                    "old_code_output": old_out[-400:]}
        if any(o == "error" for o in old.values()) and not any(o == "failure" for o in old.values()):
            return {"status": "not_proved", "tests": py_tests, "old_code_output": old_out[-400:],
                    "note": "the new tests could not even run on the old code (import or collection error), so they do not show the bug; "
                            "test the behaviour without importing names the fix adds"}
        if any(o == "failure" for o in old.values()):
            return {"status": "not_proved", "tests": py_tests,
                    "note": "the tests that fail on the old code also fail on the new code (or were already failing), so they do not prove the fix"}
        return {"status": "not_proved", "note": "the new tests also PASS on the old code, so they do not prove the bug", "tests": py_tests}
    # ---- coarse fallback: the whole test command must fail on the old code
    saved = {}
    try:
        saved = _put_old_sources_back(repo, ecp.base_commit, sources)
        ok, out = run_cmd(repo, cfg.test_command)
    finally:
        _restore(repo, saved)
    if ok:
        return {"status": "not_proved", "note": "the new tests also PASS on the old code, so they do not prove the bug", "tests": tests}
    return {"status": "proved", "note": "tests fail on the old code and pass on the new code (coarse: whole test command, not only the new tests)",
            "tests": tests, "old_code_output": out[-400:]}
