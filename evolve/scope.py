"""Scope rules: which files a change is allowed to touch.

Plain idea: the ECP lists the files Pi may change (allowed) and files it must
never touch (forbidden). Some places are ALWAYS off limits (.git, .evolution)
so an agent can never edit its own rules or the evidence trail.

The same rules are re-implemented in guard/guard-core.mjs (for Pi). Both are
tested against the same file, tests/scope_cases.json, so they cannot drift.
"""
from __future__ import annotations

import posixpath
import re
from typing import Iterable

ALWAYS_PROTECTED = [".git/**", ".evolution/**"]


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Turn a glob like 'src/**/*.py' into a regex. '**' crosses folders, '*' does not."""
    p = pattern.strip().replace("\\", "/")
    if p.endswith("/"):
        p += "**"
    out, i = "", 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif p.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile("^" + out + "$")


def normalize(path: str) -> str | None:
    """Clean a path to repo-relative form. Returns None if it escapes the repo."""
    p = path.strip().replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:/", p):
        return None  # absolute paths are never in scope
    p = posixpath.normpath(p)
    if p == ".." or p.startswith("../"):
        return None
    return p


def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(_glob_to_regex(pat).match(path) for pat in patterns)


def check_path(path: str, allowed: list[str], forbidden: list[str]) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means the edit must be blocked."""
    p = normalize(path)
    if p is None:
        return False, f"'{path}' is outside the repository"
    if matches(p, ALWAYS_PROTECTED):
        return False, f"'{p}' is a protected location (.git / .evolution)"
    if matches(p, forbidden):
        return False, f"'{p}' is listed as forbidden in the ECP"
    if not matches(p, allowed):
        return False, f"'{p}' is not in the ECP's allowed paths"
    return True, "ok"


def check_changed_files(files: Iterable[str], allowed: list[str], forbidden: list[str]) -> list[str]:
    """Scope check on a finished diff. Returns a list of violation messages (empty = clean)."""
    problems = []
    for f in files:
        ok, why = check_path(f, allowed, forbidden)
        if not ok:
            problems.append(why)
    return problems
