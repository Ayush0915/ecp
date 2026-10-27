"""Goldfish: independent reviewers with NO memory of how the plan was made.

Each reviewer gets ONE small temporary folder containing only:
  - the ECP itself
  - the repo's rules.md
  - the source files the ECP names (allowed paths, size capped)
  - (after the change) the diff and the test output
It never sees the request chat, Elephant's reasoning, or any other reviewer's
opinion. The prompt is built ONLY from that folder, so what the folder holds
is exactly what the reviewer knows (the "context contract").
"""
from __future__ import annotations

import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from ..config import Config
from ..ecp import ECP
from ..llm import LLM, extract_json
from ..scope import matches

LENS_DIR = Path(__file__).parent / "lenses"
MAX_FILE_BYTES = 20_000
MAX_FILES = 25

Severity = Literal["blocker", "major", "nit"]


class Finding(BaseModel):
    reviewer: str = ""
    severity: Severity
    location: str = ""
    claim: str
    suggested_fix: str = ""


class Lens(BaseModel):
    name: str
    phase: Literal["before", "after"]
    applies_to: list[str]  # change types
    model_key: str = "goldfish"  # which model setting to use (config.models.<key>)


LENSES = [
    Lens(name="comprehension", phase="before", applies_to=["bug", "feature", "enhancement", "refactor"]),
    Lens(name="intent_fit", phase="before", applies_to=["bug", "feature", "enhancement", "refactor"]),
    Lens(name="verification", phase="before", applies_to=["bug", "feature", "enhancement", "refactor"]),
    Lens(name="scope_rules", phase="before", applies_to=["bug", "feature", "enhancement", "refactor"]),
    Lens(name="readiness", phase="before", applies_to=["bug", "feature", "enhancement", "refactor"]),
    Lens(name="root_cause", phase="before", applies_to=["bug"]),
    Lens(name="impact", phase="before", applies_to=["feature", "enhancement"]),
    Lens(name="behavior_preserved", phase="before", applies_to=["refactor"]),
    Lens(name="conformance", phase="after", applies_to=["bug", "feature", "enhancement", "refactor"]),
    Lens(name="correctness", phase="after", applies_to=["bug", "feature", "enhancement", "refactor"], model_key="correctness"),
    Lens(name="security", phase="after", applies_to=["feature", "enhancement"]),
    Lens(name="performance", phase="after", applies_to=["enhancement"]),
]


def roster(change_type: str, phase: str) -> list[Lens]:
    """Which reviewers to call. Chosen by plain rules, not by an AI."""
    return [l for l in LENSES if l.phase == phase and change_type in l.applies_to]


def build_context(repo: Path, ecp: ECP, phase: str, extra: dict[str, str] | None = None) -> Path:
    """Create the temporary folder a reviewer is allowed to see. Returns its path."""
    d = Path(tempfile.mkdtemp(prefix=f"goldfish-{ecp.id}-"))
    (d / "ECP.md").write_text(ecp.to_markdown() if not ecp.body else ecp.to_markdown(), encoding="utf-8")
    rules = Path(repo) / ".evolution" / "rules.md"
    if rules.exists():
        shutil.copy(rules, d / "RULES.md")
    n = 0
    for f in sorted(Path(repo).rglob("*")):
        if n >= MAX_FILES:
            break
        rel = f.relative_to(repo).as_posix()
        if rel.startswith((".git/", ".evolution/", ".venv/", "node_modules/")) or not f.is_file():
            continue
        if matches(rel, ecp.allowed_paths) and f.stat().st_size <= MAX_FILE_BYTES:
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            dest = d / "files" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            n += 1
    for name, content in (extra or {}).items():
        (d / name).write_text(content, encoding="utf-8")
    return d


def render_context(d: Path) -> str:
    """The ONLY text a reviewer gets: the contents of its temp folder."""
    parts = []
    for f in sorted(d.rglob("*")):
        if f.is_file():
            parts.append(f"===== {f.relative_to(d).as_posix()} =====\n{f.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


COMMON = (Path(__file__).parent / "lenses" / "_common.md").read_text(encoding="utf-8")


def _parse(lens: Lens, raw: str) -> list[Finding]:
    try:
        data = extract_json(raw)
        if isinstance(data, dict):
            data = data.get("findings", [])
        out = []
        for item in data:
            item["reviewer"] = lens.name
            out.append(Finding(**item))
        return out
    except (ValueError, ValidationError, TypeError) as e:
        # Fail closed: an unreadable review is NOT treated as "all clear".
        return [Finding(reviewer=lens.name, severity="major", claim=f"Reviewer output could not be read ({e}); treat as not reviewed.")]


def review(llm: LLM, cfg: Config, repo: Path, ecp: ECP, phase: str, extra: dict[str, str] | None = None) -> list[Finding]:
    """Run every reviewer for this phase in parallel, return all findings."""
    lenses = roster(ecp.change_type, phase)
    ctx = build_context(repo, ecp, phase, extra)
    try:
        context_text = render_context(ctx)

        def one(lens: Lens) -> list[Finding]:
            system = COMMON + "\n\n" + (LENS_DIR / f"{lens.name}.md").read_text(encoding="utf-8")
            model = getattr(cfg.models, lens.model_key) or cfg.models.goldfish
            raw = llm.complete(f"goldfish:{lens.name}", system, context_text, model)
            return _parse(lens, raw)

        with ThreadPoolExecutor(max_workers=len(lenses)) as ex:
            results = list(ex.map(one, lenses))
    finally:
        shutil.rmtree(ctx, ignore_errors=True)
    return [f for r in results for f in r]


def blocking(findings: list[Finding]) -> list[Finding]:
    """Findings that stop the change: blockers and majors. Nits are only recorded."""
    return [f for f in findings if f.severity in ("blocker", "major")]
