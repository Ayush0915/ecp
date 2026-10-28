"""Discovery: find what is worth evolving, with evidence.  (Elephant's first job; it changes no code.)

Flow:  plain programs collect SIGNALS  ->  Elephant explains them as FINDINGS  ->  a program checks the quoted code
really exists  ->  a zero-context Goldfish checks the claim  ->  findings are scored and ranked  ->  saved as files.

Metrics (v1, Python repos): security (bandit), quality (ruff), testing (coverage), performance (slow tests),
correctness (failing tests), plus built-in checks that need no install (secrets, TODO/FIXME, git hotspots).
A tool that is not installed is reported as "skipped", never as a failure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from .config import Config, git
from .elephant import SKIP_DIRS, repo_overview
from .live import emit
from .llm import LLM, extract_json
from .verify import is_test_file, run_cmd

METRICS = ["security", "quality", "testing", "performance", "correctness"]
IMPACT = {"high": 3, "medium": 2, "low": 1}
SIZE = {"small": 1, "medium": 2, "large": 3}
MAX_PER_TOOL = 25
SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "looks like an AWS access key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key in the repo"),
    (re.compile(r"""(?i)\b(?:api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['"][^'"\s]{8,}['"]"""), "hardcoded credential-like value"),
]
TEXT_SUFFIX = (".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".txt", ".md", ".sh")


# ------------------------------------------------------------------ helpers
def _files(repo: Path):
    for f in sorted(Path(repo).rglob("*")):
        rel = f.relative_to(repo).as_posix()
        if f.is_file() and not (SKIP_DIRS & set(rel.split("/")[:-1])) and f.stat().st_size < 300_000:
            yield f, rel


def _snippet(repo: Path, rel: str, line: int, radius: int = 2) -> str:
    try:
        lines = (Path(repo) / rel).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    lo, hi = max(1, line - radius), min(len(lines), line + radius)
    return "\n".join(f"{n}: {lines[n - 1]}" for n in range(lo, hi + 1))


def _rel(repo: Path, p: str) -> str:
    p = p.replace("\\", "/")
    try:
        return Path(p).resolve().relative_to(Path(repo).resolve()).as_posix() if os.path.isabs(p) else p.removeprefix("./")
    except ValueError:
        return p


def _tool(repo: Path, args: list[str], timeout: int = 300) -> tuple[int, str, str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    r = subprocess.run([sys.executable, "-m", *args], cwd=repo, capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout, r.stderr


def _missing(stderr: str, module: str) -> bool:
    return f"No module named {module}" in stderr or f"No module named '{module}'" in stderr


class Signals:
    def __init__(self):
        self.items: list[dict] = []
        self.detectors: dict[str, dict] = {}

    def add(self, metric, tool, rule, file, line, message, severity="medium", evidence="tool", lines=None):
        self.items.append({"id": f"S-{len(self.items) + 1}", "metric": metric, "tool": tool, "rule": rule, "file": file,
                           "line": int(line or 1), "message": message, "severity": severity, "kind": evidence, "lines": lines or []})
        return self.items[-1]["id"]

    def status(self, name, state, note=""):
        self.detectors[name] = {"state": state, "note": note}


# ------------------------------------------------------------------ detectors (plain programs, no AI)
def d_tests(repo: Path, cfg: Config, sig: Signals) -> None:
    if not cfg.test_command:
        sig.status("tests", "skipped", "no test command configured: nothing can be verified or reproduced")
        return
    ok, out = run_cmd(repo, cfg.test_command)
    if ok:
        sig.status("tests", "ran", "baseline passes")
        return
    sig.status("tests", "ran", "baseline FAILS")
    m = re.findall(r"^(?:FAILED|ERROR)\s+(\S+?)::(\S+)", out, re.M)
    if m:
        for f, name in m[:10]:
            sig.add("correctness", "tests", "failing-test", _rel(repo, f), 1, f"test {name} fails on the current code", "high", "reproduced")
    else:
        sig.add("correctness", "tests", "failing-suite", "", 1, "the test suite fails: " + out.strip()[-300:], "high", "reproduced")


def d_bandit(repo: Path, cfg: Config, sig: Signals) -> None:
    try:
        code, out, err = _tool(repo, ["bandit", "-r", ".", "-f", "json", "-q", "-x", "./.venv,./venv,./.evolution,./node_modules,./tests"])
    except subprocess.TimeoutExpired:
        sig.status("security (bandit)", "skipped", "timed out"); return
    if _missing(err, "bandit"):
        sig.status("security (bandit)", "skipped", "not installed: pip install bandit"); return
    try:
        res = json.loads(out)["results"]
    except (ValueError, KeyError):
        sig.status("security (bandit)", "skipped", "could not read bandit output"); return
    sig.status("security (bandit)", "ran", f"{len(res)} alert(s)")
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    # asserts and anything in test files are not security findings; keeping them would bury the real ones
    res = [r for r in res if r["test_id"] != "B101" and not is_test_file(_rel(repo, r["filename"]))]
    sig.status("security (bandit)", "ran", f"{len(res)} alert(s) after ignoring asserts and test files")
    for r in sorted(res, key=lambda r: rank.get(r.get("issue_severity", "LOW"), 2))[:MAX_PER_TOOL]:
        sig.add("security", "bandit", r["test_id"], _rel(repo, r["filename"]), r["line_number"], r["issue_text"], r["issue_severity"].lower())


def d_ruff(repo: Path, cfg: Config, sig: Signals) -> None:
    try:
        code, out, err = _tool(repo, ["ruff", "check", ".", "--output-format", "json", "--no-cache", "--exclude", ".venv,venv,.evolution,node_modules"])
    except subprocess.TimeoutExpired:
        sig.status("quality (ruff)", "skipped", "timed out"); return
    if _missing(err, "ruff"):
        sig.status("quality (ruff)", "skipped", "not installed: pip install ruff"); return
    try:
        res = json.loads(out)
    except ValueError:
        sig.status("quality (ruff)", "skipped", "could not read ruff output"); return
    res = [r for r in res if not is_test_file(_rel(repo, r["filename"]))]
    sig.status("quality (ruff)", "ran", f"{len(res)} issue(s) outside test files")
    for r in res[:MAX_PER_TOOL]:
        sig.add("quality", "ruff", r.get("code") or "ruff", _rel(repo, r["filename"]), r["location"]["row"], r["message"], "low")


def d_coverage_and_speed(repo: Path, cfg: Config, sig: Signals) -> None:
    if "pytest" not in cfg.test_command:
        why = "needs a pytest-based test command"
        sig.status("testing (coverage)", "skipped", why); sig.status("performance (slow tests)", "skipped", why); return
    tmp = Path(tempfile.mkdtemp(prefix="evolve-cov-"))
    env_file = tmp / ".coverage"
    env = {**os.environ, "COVERAGE_FILE": str(env_file), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        py = cfg.python or sys.executable
        r = subprocess.run([py, "-m", "coverage", "run", "--source=.", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                            "--durations=10", "--durations-min=0.2"], cwd=repo, capture_output=True, text=True, timeout=600, env=env)
    except subprocess.TimeoutExpired:
        sig.status("testing (coverage)", "skipped", "timed out"); sig.status("performance (slow tests)", "skipped", "timed out"); return
    if _missing(r.stderr + r.stdout, "coverage"):
        sig.status("testing (coverage)", "skipped", "not installed: pip install coverage")
    else:
        j = tmp / "cov.json"
        subprocess.run([py, "-m", "coverage", "json", "-o", str(j)], cwd=repo, capture_output=True, env=env)
        try:
            files = json.loads(j.read_text())["files"]
        except (OSError, ValueError, KeyError):
            files = None
        if files is None:
            sig.status("testing (coverage)", "skipped", "no coverage data (did the tests run?)")
        else:
            low = []
            for f, d in files.items():
                rel = _rel(repo, f)
                if is_test_file(rel) or rel.endswith("__init__.py") or SKIP_DIRS & set(rel.split("/")):
                    continue
                pct, miss = d["summary"]["percent_covered"], d.get("missing_lines", [])
                if miss and pct < 80:
                    low.append((pct, rel, miss))
            sig.status("testing (coverage)", "ran", f"{len(low)} file(s) under 80% covered")
            for pct, rel, miss in sorted(low)[:MAX_PER_TOOL]:
                sig.add("testing", "coverage", "low-coverage", rel, miss[0], f"only {pct:.0f}% of this file is run by tests; lines never run: {_ranges(miss)}", "medium", lines=miss)
    slow = re.findall(r"^\s*(\d+\.\d+)s call\s+(\S+)", r.stdout, re.M)
    sig.status("performance (slow tests)", "ran", f"{len(slow)} test(s) over 0.2s")
    for secs, name in slow[:5]:
        f = name.split("::")[0]
        sig.add("performance", "pytest-durations", "slow-test", f, 1, f"test {name.split('::')[-1]} takes {secs}s: the code it runs may be slow", "low", "measured")


def _ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in nums + [None]:
        if start is None:
            start = prev = n
        elif n is not None and n == prev + 1:
            prev = n
        else:
            out.append(str(start) if start == prev else f"{start}-{prev}")
            start = prev = n
    return ", ".join(out[:8]) + ("..." if len(out) > 8 else "")


def d_builtin(repo: Path, cfg: Config, sig: Signals) -> None:
    secrets = todos = 0
    for f, rel in _files(repo):
        if not rel.endswith(TEXT_SUFFIX) and Path(rel).name != ".env":
            continue
        if is_test_file(rel) or Path(rel).name == ".env.example":
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(lines, 1):
            for pat, why in SECRET_PATTERNS:
                if pat.search(line) and secrets < MAX_PER_TOOL:
                    sig.add("security", "builtin-secrets", "secret", rel, n, why, "high"); secrets += 1; break
            if rel.endswith((".py", ".js", ".ts")) and re.search(r"#\s*(TODO|FIXME|HACK)\b|//\s*(TODO|FIXME|HACK)\b", line) and todos < 10:
                sig.add("quality", "builtin-todo", "todo", rel, n, "unfinished-work marker: " + line.strip()[:100], "low"); todos += 1
    sig.status("secrets scan (built in)", "ran", f"{secrets} hit(s)")
    sig.status("TODO/FIXME scan (built in)", "ran", f"{todos} marker(s)")


def d_churn(repo: Path, cfg: Config, sig: Signals) -> None:
    count = git(repo, "rev-list", "--count", "HEAD", check=False)
    if not count.isdigit() or int(count) < 5:
        sig.status("git hotspots (built in)", "skipped", "needs at least 5 commits of history"); return
    out = git(repo, "log", "-n", "300", "--numstat", "--format=", check=False)
    churn: dict[str, int] = {}
    for line in out.splitlines():
        p = line.split("\t")
        if len(p) == 3 and p[0].isdigit() and p[1].isdigit():
            churn[p[2]] = churn.get(p[2], 0) + int(p[0]) + int(p[1])
    rel_churn = []
    for f, c in churn.items():
        full = Path(repo) / f
        if full.is_file() and f.endswith(".py") and not is_test_file(f):
            size = max(1, len(full.read_text(encoding="utf-8", errors="ignore").splitlines()))
            rel_churn.append((c / size, f, c, size))
    top = sorted(rel_churn, reverse=True)[:3]
    sig.status("git hotspots (built in)", "ran", f"{len(top)} hotspot(s)")
    for ratio, f, c, size in top:  # relative churn (changes per line of code), not raw volume
        sig.add("quality", "git", "hotspot", f, 1, f"changed {c} lines over {size} lines of code in recent history ({ratio:.1f}x): a file that keeps changing", "low")


DETECTORS: list[Callable] = [d_tests, d_bandit, d_ruff, d_coverage_and_speed, d_builtin, d_churn]


def collect_signals(repo: Path, cfg: Config, log: Callable[[str], None] = lambda s: None) -> Signals:
    sig = Signals()
    for d in DETECTORS:
        log(f"  detector: {d.__name__[2:]}")
        try:
            d(Path(repo), cfg, sig)
        except Exception as e:  # a broken detector must never stop discovery
            sig.status(d.__name__[2:], "skipped", f"error: {str(e)[:120]}")
    return sig


# ------------------------------------------------------------------ Elephant: scout
SCOUT_SYSTEM = """You are Elephant in SCOUT mode. You never write code and never change files. You decide which problems in this repository are real and worth fixing.
You are given SIGNALS (output of plain programs: tests, bandit, ruff, coverage, timings) and repository files.

Reply with ONLY a JSON object: {"findings": [ ... ]}  (at most 8 findings, most valuable first). Each finding:
 {"title": str, "metric": "security"|"quality"|"testing"|"performance"|"correctness",
  "change_type": "bug"|"enhancement"|"refactor",
  "signal_ids": [ids from SIGNALS that support it, or [] if this is your own reading of the code],
  "file": repo-relative path, "quote": ONE line copied EXACTLY from that file that shows the problem,
  "explanation": 1-3 plain sentences: what is wrong and what could go wrong,
  "impact": "high"|"medium"|"low", "impact_reason": str, "fix_size": "small"|"medium"|"large"}
Rules: only report what the evidence shows. Merge signals about the same problem into one finding. Ignore noise (style nits with no consequence).
A bug needs a real wrong behaviour you can point at. Do not invent problems. If nothing is worth fixing, return {"findings": []}.
Use metric "testing" for untested code (change_type "enhancement"), "performance" only if the code is measurably slow, "correctness" for wrong behaviour."""

CLAIM_SYSTEM = """You are an independent reviewer with no other context. You get ONE claim about a code file and the file itself.
Decide whether the claim is TRUE from the file alone. Reply with ONLY JSON: {"true": true|false, "reason": "one sentence"}.
If the file does not support the claim, answer false."""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def quote_line(repo: Path, rel: str, quote: str) -> int:
    """Line number where the quoted text really is, or 0. A plain check; no AI."""
    try:
        lines = (Path(repo) / rel).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return 0
    q = _norm(quote)
    if not q:
        return 0
    for n, line in enumerate(lines, 1):
        if q in _norm(line):
            return n
    return 0


def linked_signals(finding: dict, line: int, signals: dict[str, dict]) -> list[dict]:
    """Real signals that point at the same place: ones the model named (same file) plus ones on that file within a few lines
    of the quoted code. Found by program, so the model can neither inflate nor forget evidence."""
    out = {}
    for i in finding.get("signal_ids", []):
        s = signals.get(i)
        if s and s["file"] in ("", finding["file"]) and s["metric"] == finding["metric"]:
            out[s["id"]] = s
    for s in signals.values():  # only signals of the SAME metric count: a style warning is not evidence of a bug
        if s["metric"] == finding["metric"] and s["file"] == finding["file"] and (abs(s["line"] - line) <= 3 or line in s.get("lines", [])):
            out[s["id"]] = s
    return list(out.values())


def evidence_level(sigs: list[dict]) -> tuple[int, str]:
    """Computed by program from the real signals, never taken from the model's word."""
    if any(s["kind"] == "reproduced" for s in sigs):
        return 3, "reproduced: a test fails on the current code"
    if any(s["kind"] in ("tool", "measured") for s in sigs):
        return 2, "a tool flagged this exact spot" + (" and measured it" if any(s["kind"] == "measured" for s in sigs) else "")
    return 1, "AI reading only (the quoted code exists, but no tool backs the claim)"


def _json_call(llm: LLM, role: str, system: str, user: str, model: str) -> dict:
    try:
        return extract_json(llm.complete(role, system, user, model))
    except (ValueError, TypeError):
        return extract_json(llm.complete(role, system, user + "\n\nYour previous reply was not valid JSON. Reply again with ONLY the JSON object.", model))


def next_finding_number(repo: Path) -> int:
    d = Path(repo) / ".evolution" / "findings"
    nums = [int(p.stem.split("-")[1]) for p in d.glob("F-*.json")] if d.exists() else []
    return max(nums, default=0) + 1


def discover(repo: Path, llm: LLM, cfg: Config, log: Callable[[str], None] = lambda s: None) -> dict:
    repo = Path(repo)
    run_id = time.strftime("D-%Y%m%d-%H%M%S")
    log("1/4 Collecting signals with plain programs (no AI)...")
    emit(repo, "signals_start")
    sig = collect_signals(repo, cfg, log)
    by_id = {s["id"]: s for s in sig.items}
    for name, d in sig.detectors.items():
        log(f"  {name}: {d['state']}  {d['note']}")
    emit(repo, "signals", detectors=sig.detectors, count=len(sig.items), items=[{k: v for k, v in x.items() if k != "lines"} for x in sig.items][:60])

    log("2/4 Elephant (scout) is reading the signals and the code...")
    sigtext = "\n\n".join(f"{s['id']} [{s['metric']}/{s['tool']} {s['rule']}] {s['file']}:{s['line']} severity={s['severity']}\n  {s['message']}\n"
                          + "\n".join("  | " + l for l in _snippet(repo, s['file'], s['line']).splitlines()) for s in sig.items) or "(no signals)"
    user = f"SIGNALS:\n{sigtext}\n\nPAST ECPs:\n{_history(repo)}\n\nREPO FILES:\n{repo_overview(repo, cfg.context_bytes)}"
    emit(repo, "elephant_start", task="scout", model=cfg.models.resolve("elephant"))
    raw = _json_call(llm, "elephant:scout", SCOUT_SYSTEM, user, cfg.models.elephant).get("findings", [])
    log(f"  Elephant proposed {len(raw)} finding(s)")
    emit(repo, "elephant_done", task="scout", proposed=[{"title": str(f.get("title", "?")), "file": str(f.get("file", "")), "metric": str(f.get("metric", ""))} for f in raw])

    log("3/4 Checking every finding (quote exists, then an independent reviewer)...")
    kept, dropped = [], []
    for f in raw:
        try:
            metric = f["metric"] if f["metric"] in METRICS else "quality"
            rel = _rel(repo, str(f["file"]))
            line = quote_line(repo, rel, str(f["quote"]))
            if not line:
                dropped.append({"title": f.get("title", "?"), "why": "the quoted code was not found in that file"}); continue
            ctext = (repo / rel).read_text(encoding="utf-8")[:20000]
            claim = f"{f['title']}. {f['explanation']} (see: {f['quote'].strip()})"
            emit(repo, "goldfish_start", lens="claim", phase="discover", model=cfg.models.resolve("goldfish"), subject=str(f["title"]))
            try:
                verdict = _json_call(llm, "goldfish:claim", CLAIM_SYSTEM, f"CLAIM:\n{claim}\n\nFILE {rel}:\n{ctext}", cfg.models.goldfish)
                is_true, why = bool(verdict.get("true")), str(verdict.get("reason", ""))
            except Exception:
                is_true, why = False, "reviewer answer unreadable (fails closed)"
            emit(repo, "goldfish_done", lens="claim", phase="discover", subject=str(f["title"]), verdict=is_true, reason=why, findings=[])
            if not is_true:
                dropped.append({"title": f["title"], "why": "independent reviewer says the claim is not supported: " + why}); continue
            linked = linked_signals({**f, "file": rel, "metric": metric}, line, by_id)
            ev, ev_why = evidence_level(linked)
            kept.append({
                "title": str(f["title"]), "metric": metric, "change_type": f.get("change_type", "bug") if f.get("change_type") in ("bug", "enhancement", "refactor") else "bug",
                "file": rel, "line": line, "quote": str(f["quote"]).strip(), "explanation": str(f["explanation"]),
                "signals": [{k: v for k, v in s.items() if k != "lines"} for s in linked],
                "evidence": ev, "evidence_why": ev_why, "claim_check": why,
                "impact": f.get("impact") if f.get("impact") in IMPACT else "medium", "impact_reason": str(f.get("impact_reason", "")),
                "fix_size": f.get("fix_size") if f.get("fix_size") in SIZE else "medium",
                "measurable": metric != "performance" or ev >= 2,
            })
        except (KeyError, TypeError, AttributeError) as e:
            dropped.append({"title": str(f)[:60], "why": f"malformed finding ({e})"})
    kept.sort(key=lambda k: (-k["evidence"], -IMPACT[k["impact"]], SIZE[k["fix_size"]]))
    for k in dropped:
        log(f"  dropped: {k['title']} - {k['why']}")

    log("4/4 Saving findings...")
    out_dir = repo / ".evolution" / "findings"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = next_finding_number(repo)
    saved = []
    for rank, k in enumerate(kept, 1):
        k.update({"id": f"F-{n:03d}", "run": run_id, "rank": rank, "status": "open", "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
        (out_dir / f"{k['id']}.json").write_text(json.dumps(k, indent=1), encoding="utf-8")
        saved.append(k); n += 1
    summary = {"run": run_id, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "detectors": sig.detectors, "signals": len(sig.items),
               "proposed": len(raw), "kept": [k["id"] for k in saved], "dropped": dropped}
    (repo / ".evolution" / "baseline.json").write_text(json.dumps({**summary, "signal_list": sig.items}, indent=1), encoding="utf-8")
    with (repo / ".evolution" / "discovery.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    summary["findings"] = saved
    log(f"  {len(saved)} finding(s) kept, {len(dropped)} dropped")
    emit(repo, "discovery_done", kept=[{k: v for k, v in x.items() if k not in ("signals",)} for x in saved], dropped=dropped)
    return summary


def _history(repo: Path) -> str:
    from .elephant import history
    return history(repo)


# ------------------------------------------------------------------ using a finding
def list_findings(repo: Path, only_open: bool = False) -> list[dict]:
    d = Path(repo) / ".evolution" / "findings"
    out = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("F-*.json"))] if d.exists() else []
    out = [f for f in out if f["status"] == "open"] if only_open else out
    return sorted(out, key=lambda f: (f.get("run", ""), -f.get("rank", 0)), reverse=True)


def load_finding(repo: Path, fid: str) -> dict:
    p = Path(repo) / ".evolution" / "findings" / f"{fid}.json"
    if not p.exists():
        raise ValueError(f"No finding {fid}. Run `evolve discover` first.")
    return json.loads(p.read_text(encoding="utf-8"))


def request_from_finding(f: dict) -> str:
    return (f"Finding {f['id']} (metric: {f['metric']}, suggested change type: {f['change_type']}, evidence {f['evidence']}/3: {f['evidence_why']}).\n"
            f"{f['title']}\n{f['explanation']}\nWhere: {f['file']}:{f['line']}  ->  {f['quote']}\n"
            + "".join(f"Signal {s['id']} ({s['tool']} {s['rule']}): {s['message']}\n" for s in f.get("signals", [])))


def mark_finding(repo: Path, fid: str, status: str, ecp_id: str = "") -> None:
    p = Path(repo) / ".evolution" / "findings" / f"{fid}.json"
    if p.exists():
        f = json.loads(p.read_text(encoding="utf-8"))
        f["status"] = status
        if ecp_id:
            f["ecp"] = ecp_id
        p.write_text(json.dumps(f, indent=1), encoding="utf-8")


def format_findings(summary: dict) -> str:
    lines = []
    for k in summary["findings"]:
        lines.append(f"{k['id']}  [{k['metric']}/{k['change_type']}]  evidence {k['evidence']}/3  impact {k['impact']}  fix {k['fix_size']}\n"
                     f"       {k['title']}\n       {k['file']}:{k['line']}  {k['quote']}\n       why: {k['explanation']}")
    return "\n".join(lines) or "No findings passed the checks."
