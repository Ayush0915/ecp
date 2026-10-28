"""A live feed of what is happening, for the dashboard.

Every step of a run writes one small event (Elephant started, this Goldfish finished, Pi edited that file...) to
.evolution/run/live.jsonl while it happens. When a run ends the feed is saved as .evolution/events/<id>.jsonl, so any
past run can be replayed exactly as it unfolded. Events are for watching; the hash-chained evidence log stays the record.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_counts: dict[str, int] = {}


def _path(repo) -> Path:
    return Path(repo) / ".evolution" / "run" / "live.jsonl"


def reset(repo, job: str, label: str = "") -> None:
    p = _path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        p.write_text("", encoding="utf-8")
        _counts[str(p)] = 0
    emit(repo, "job_start", job=job, label=label)


def emit(repo, kind: str, **data) -> None:
    """Never raises: watching must not be able to break a run."""
    try:
        p = _path(repo)
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            n = _counts.get(str(p))
            if n is None:
                n = sum(1 for _ in p.open("r", encoding="utf-8")) if p.exists() else 0
            line = json.dumps({"seq": n + 1, "t": round(time.time(), 3), "kind": kind, "data": data}, ensure_ascii=False)
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            _counts[str(p)] = n + 1
    except Exception:  # noqa: BLE001
        pass


def read(repo, after: int = 0, ecp: str = "") -> list[dict]:
    p = Path(repo) / ".evolution" / "events" / f"{ecp}.jsonl" if ecp else _path(repo)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e["seq"] > after:
            out.append(e)
    return out


def save_as(repo, name: str) -> None:
    """Keep this run's feed for replay."""
    try:
        src = _path(repo)
        if src.exists():
            dst = Path(repo) / ".evolution" / "events" / f"{name}.jsonl"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
