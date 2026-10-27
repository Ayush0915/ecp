"""The ECP (Evolution Candidate Profile).

One file per proposed change: .evolution/ecps/ECP-0001.md
  - top part  = YAML header (the PLAN: intent, scope, rules, success criteria...)
  - bottom    = Markdown body (the reasoning, root cause, approach)
The plan part is LOCKED (hashed) when approved. Any later edit is detected.
The RECORD part lives next to it in .evolution/evidence/ECP-0001.jsonl and is
append-only; every line carries the hash of the line before it (a tiny chain),
so tampering with history is detectable.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

ChangeType = Literal["bug", "feature", "enhancement", "refactor"]
Risk = Literal["low", "high"]
Status = Literal["proposed", "reviewed", "approved", "executing", "verified", "decided"]


class Check(BaseModel):
    """One way to prove the change worked."""
    description: str
    command: str = ""  # shell command that must exit 0; empty = covered by the main test command


class ECP(BaseModel):
    # ---- identity ----
    id: str
    title: str
    change_type: ChangeType
    status: Status = "proposed"
    parent: str = ""
    base_commit: str = ""
    # ---- the plan (frozen on approval) ----
    intent: str
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    verification: list[Check] = Field(default_factory=list)
    abort_conditions: list[str] = Field(default_factory=list)
    rollback: str = "Discard the evolution branch and return to the base commit."
    risk: Risk = "low"
    # ---- lock ----
    plan_hash: str = ""
    # ---- free text (part of the plan, hashed too) ----
    body: str = ""

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not v.startswith("ECP-"):
            raise ValueError("id must look like ECP-0001")
        return v

    # ------------------------------------------------------------------ checks
    def problems(self) -> list[str]:
        """Basic completeness check (no AI involved). Empty list = well formed."""
        out = []
        if len(self.intent.strip()) < 10:
            out.append("intent is missing or too short")
        if not self.allowed_paths:
            out.append("allowed_paths is empty: Pi would have nothing it may edit")
        if not self.success_criteria:
            out.append("success_criteria is empty: nobody could tell if the change worked")
        if not self.verification:
            out.append("verification is empty: no way to prove success")
        if not self.abort_conditions:
            out.append("abort_conditions is empty: no rule for when to stop")
        overlap = set(self.allowed_paths) & set(self.forbidden_paths)
        if overlap:
            out.append(f"paths are both allowed and forbidden: {sorted(overlap)}")
        return out

    # ------------------------------------------------------------------ locking
    def _plan_payload(self) -> str:
        d = self.model_dump(exclude={"status", "plan_hash"})
        return json.dumps(d, sort_keys=True, ensure_ascii=False)

    def compute_hash(self) -> str:
        return hashlib.sha256(self._plan_payload().encode()).hexdigest()[:16]

    def lock(self) -> None:
        self.plan_hash = self.compute_hash()

    def is_locked(self) -> bool:
        return bool(self.plan_hash)

    def lock_intact(self) -> bool:
        return self.is_locked() and self.plan_hash == self.compute_hash()

    # ------------------------------------------------------------------ files
    def to_markdown(self) -> str:
        head = self.model_dump(exclude={"body"})
        return "---\n" + yaml.safe_dump(head, sort_keys=False, allow_unicode=True) + "---\n\n" + self.body.strip() + "\n"

    @classmethod
    def from_markdown(cls, text: str) -> "ECP":
        if not text.startswith("---"):
            raise ValueError("ECP file must start with a YAML header (---)")
        _, header, body = text.split("---", 2)
        data = yaml.safe_load(header) or {}
        data["body"] = body.strip()
        return cls(**data)

    def save(self, repo: Path) -> Path:
        d = Path(repo) / ".evolution" / "ecps"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.id}.md"
        p.write_text(self.to_markdown(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: Path) -> "ECP":
        return cls.from_markdown(Path(path).read_text(encoding="utf-8"))


def save_version(repo: Path, ecp: "ECP", n: int) -> Path:
    """Keep every version of the plan (v1 = first draft, v2 = after the first revision...). Used to show the plan evolving."""
    d = Path(repo) / ".evolution" / "versions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ecp.id}.v{n}.md"
    p.write_text(ecp.to_markdown(), encoding="utf-8")
    return p


def list_versions(repo: Path, ecp_id: str) -> list[tuple[int, str]]:
    d = Path(repo) / ".evolution" / "versions"
    items = []
    for f in d.glob(f"{ecp_id}.v*.md") if d.exists() else []:
        items.append((int(f.stem.rsplit(".v", 1)[1]), f.read_text(encoding="utf-8")))
    return sorted(items)


def next_id(repo: Path) -> str:
    d = Path(repo) / ".evolution" / "ecps"
    nums = [int(p.stem.split("-")[1]) for p in d.glob("ECP-*.md")] if d.exists() else []
    return f"ECP-{(max(nums) + 1 if nums else 1):04d}"


# ---------------------------------------------------------------------- evidence
class Evidence:
    """Append-only, hash-chained log of everything that happened to one ECP."""

    def __init__(self, repo: Path, ecp_id: str):
        self.path = Path(repo) / ".evolution" / "evidence" / f"{ecp_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists():
            return "genesis"
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        return json.loads(lines[-1])["hash"] if lines else "genesis"

    def add(self, kind: str, data: dict | None = None) -> dict:
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "data": data or {}, "prev": self._last_hash()}
        entry["hash"] = hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:16]
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def chain_intact(self) -> bool:
        prev = "genesis"
        for e in self.entries():
            claimed = e["hash"]
            body = {k: v for k, v in e.items() if k != "hash"}
            if body["prev"] != prev:
                return False
            if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16] != claimed:
                return False
            prev = claimed
        return True
