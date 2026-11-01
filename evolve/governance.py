"""The governance profile (governance.yaml): how much the system may do on its own, for one repository.

It lives in the governance store (see store.py), never inside the repository it governs. Plain rules only: nothing
in here is decided by an AI. A typo in a key is an error, not silently ignored.

Autonomy levels
  0  discover only: findings are produced, nothing is planned or changed
  1  a human approves every plan and every result
  2  low-risk changes run on their own; high risk asks a human            (default)
  3  as level 2; later the system may also pick its own findings (C63)
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import config_path, governance_path, rules_path

ChangeType = Literal["bug", "feature", "enhancement", "refactor"]
ALL_TYPES = ["bug", "feature", "enhancement", "refactor"]
LEVELS = {
    0: "discover only: nothing is planned or changed",
    1: "a human approves every plan and every result",
    2: "low-risk changes run on their own; high risk asks a human",
    3: "as level 2; the system may also pick its own findings",
}

# Files whose change is risky by nature: dependencies, build/CI, deployment, auth, crypto, secrets.
DEFAULT_HIGH_RISK_PATHS = [
    "**/requirements*.txt", "**/pyproject.toml", "**/setup.py", "**/setup.cfg", "**/Pipfile", "**/Pipfile.lock", "**/poetry.lock",
    "**/package.json", "**/package-lock.json", "**/yarn.lock", "**/pnpm-lock.yaml", "**/go.mod", "**/go.sum", "**/Cargo.toml", "**/Cargo.lock",
    ".github/**", ".gitlab-ci.yml", "**/Dockerfile*", "**/docker-compose*.yml", "**/Makefile",
    "**/*auth*/**", "**/*auth*.*", "**/*crypt*", "**/*secret*", "**/*password*", "**/security/**", "**/.env*",
]


class GovernanceError(RuntimeError):
    """The governance profile forbids this, or the profile itself is invalid. The message says what to change."""


class Governance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    autonomy_level: int = Field(2, ge=0, le=3)
    allowed_change_types: list[ChangeType] = Field(default_factory=lambda: list(ALL_TYPES))
    max_files: int = Field(10, ge=1)                  # a plan may not put more files than this in scope (blocks the plan)
    high_risk_files: int = Field(4, ge=1)             # more files than this in scope or in the diff = high risk
    high_risk_diff_lines: int = Field(300, ge=1)      # a diff bigger than this = high risk
    max_diff_lines: int = Field(1500, ge=1)           # a diff bigger than this is rolled back
    high_risk_paths: list[str] = Field(default_factory=lambda: list(DEFAULT_HIGH_RISK_PATHS))
    human_gates: list[Literal["plan", "result"]] = Field(default_factory=list)  # gates that always ask a human
    required_lenses: dict[str, list[str]] = Field(default_factory=dict)        # extra Goldfish lenses per change type

    @field_validator("required_lenses")
    @classmethod
    def _known_lenses(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        from .goldfish.runner import LENSES
        names = {l.name for l in LENSES}
        for ctype, lenses in v.items():
            if ctype not in ALL_TYPES:
                raise ValueError(f"required_lenses: unknown change type '{ctype}' (use {', '.join(ALL_TYPES)})")
            bad = [x for x in lenses if x not in names]
            if bad:
                raise ValueError(f"required_lenses: unknown lens {bad} (known: {', '.join(sorted(names))})")
        return v

    def describe(self) -> str:
        return f"level {self.autonomy_level}: {LEVELS[self.autonomy_level]}"


HEADER = """# Governance profile for one repository. Plain rules; nothing here is decided by an AI.
# It lives outside the repository on purpose: the repository cannot change its own governance.
#
# autonomy_level: 0 discover only | 1 a human approves every plan and result
#                 2 low risk runs on its own, high risk asks a human | 3 as 2, and may pick its own findings
# max_files: a plan with more files in scope is sent back
# high_risk_files / high_risk_diff_lines: above these, a human must approve
# max_diff_lines: a bigger diff is rolled back
# high_risk_paths: touching these makes a change high risk (dependencies, CI, auth, secrets...)
# human_gates: [plan, result] to always ask a human at those gates
# required_lenses: extra Goldfish reviewers per change type, e.g. {bug: [security]}
"""


def write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + yaml.safe_dump(Governance().model_dump(), sort_keys=False), encoding="utf-8")


def load_governance(repo: Path) -> Governance:
    p = governance_path(repo)
    if not p.exists():
        return Governance()
    try:
        return Governance(**(yaml.safe_load(p.read_text(encoding="utf-8")) or {}))
    except (ValidationError, yaml.YAMLError, TypeError) as e:
        raise GovernanceError(f"{p} is not valid: {e}") from None


def save_governance(repo: Path, changes: dict) -> Governance:
    """Change some fields, validate, write. The header comments are kept."""
    p = governance_path(repo)
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    raw = {**Governance().model_dump(), **(raw or {}), **changes}
    try:
        gov = Governance(**raw)
    except ValidationError as e:
        raise GovernanceError(str(e)) from None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(HEADER + yaml.safe_dump(gov.model_dump(), sort_keys=False), encoding="utf-8")
    return gov


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def snapshot(repo: Path, gov: Governance) -> dict:
    """What governed this run, for the evidence log: the profile itself and fingerprints of the rules and config."""
    rules = rules_path(repo)
    return {"governance": gov.model_dump(), "governance_sha256": _sha(governance_path(repo)), "rules_sha256": _sha(rules),
            "config_sha256": _sha(config_path(repo)), "rules": rules.read_text(encoding="utf-8")[:4000] if rules.exists() else ""}
