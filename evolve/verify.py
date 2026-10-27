"""Verification done by plain programs, not AI: run the tests, check the diff stayed in scope."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import Config, git
from .ecp import ECP
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
    if cfg.test_command:
        r.test_ok, r.test_output = run_cmd(repo, cfg.test_command)
    else:
        r.test_ok, r.test_output = False, "No test command configured; cannot verify. Set test_command in .evolution/config.yaml."
    if ecp.change_type in cfg.require_fail_before and r.test_ok:
        r.fail_before = fail_before_check(repo, ecp, cfg, r.changed_files)
    for chk in ecp.verification:
        if chk.command:
            ok, out = run_cmd(repo, chk.command)
            r.check_results.append({"description": chk.description, "command": chk.command, "ok": ok, "output": out[-500:]})
    return r


def fail_before_check(repo: Path, ecp: ECP, cfg: Config, changed: list[str]) -> dict:
    """Prove the new tests are real: put the OLD source code back (keeping the new tests) and run the tests.
    They must FAIL there. If they pass on the old code, they prove nothing about the change.
    The working tree is always restored afterwards. No AI involved."""
    repo = Path(repo)
    tests = [f for f in changed if is_test_file(f)]
    if not tests:
        return {"status": "no_tests", "note": "this change type must add or change a test, and none was added"}
    if not cfg.test_command:
        return {"status": "not_proved", "note": "no test command configured"}
    sources = [f for f in changed if not is_test_file(f)]
    saved: dict[str, bytes | None] = {}
    try:
        for f in sources:
            path = repo / f
            saved[f] = path.read_bytes() if path.exists() else None
            old = subprocess.run(["git", "-C", str(repo), "show", f"{ecp.base_commit}:{f}"], capture_output=True)
            if old.returncode == 0:
                path.write_bytes(old.stdout)  # put the old version back
            elif path.exists():
                path.unlink()  # file did not exist before the change
        ok, out = run_cmd(repo, cfg.test_command)
    finally:
        for f, content in saved.items():
            path = repo / f
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    if ok:
        return {"status": "not_proved", "note": "the new tests also PASS on the old code, so they do not prove the bug", "tests": tests}
    return {"status": "proved", "note": "tests fail on the old code and pass on the new code", "tests": tests, "old_code_output": out[-400:]}
