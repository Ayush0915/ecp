"""Getting a repository in: turn whatever the person pasted into a local git working copy.

Accepted:  https://github.com/owner/repo   .../repo.git   .../repo/tree/branch[/folder]   github.com/owner/repo
           owner/repo (GitHub shorthand)   git@github.com:owner/repo.git   ssh://git@host/owner/repo.git
           GitLab-style links (with /-/tree/branch)   a folder on this computer (must already be a git repo)

Cloning is shallow (recent history is enough for hotspots and rollback), never prompts for a password, and a private
GitHub repo works if GITHUB_TOKEN is set (the token is sent as a header for that one command; it is never stored in the repo).
"""
from __future__ import annotations

import base64
import collections
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

DEPTH = 500
LANGS = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".go": "Go", ".rs": "Rust",
         ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cs": "C#", ".kt": "Kotlin", ".swift": "Swift", ".scala": "Scala"}


class RepoError(ValueError):
    """Something a person can fix; the message says how."""


@dataclass
class Source:
    kind: str                # "url" | "local"
    raw: str
    url: str = ""            # clean clone URL (never contains a token)
    host: str = ""
    owner: str = ""
    name: str = ""
    branch: str = ""
    rest: list = field(default_factory=list)   # path pieces after /tree/ (branch and maybe a folder)
    path: str = ""           # for local


def _clean(spec: str) -> str:
    spec = (spec or "").strip().strip('"').strip("'").strip()
    if not spec:
        raise RepoError("Paste a GitHub link (https://github.com/owner/repo) or the path of a folder.")
    if any(ord(c) < 32 for c in spec) or " " in spec and not re.match(r"^[A-Za-z]:[\\/]", spec) and not Path(spec).expanduser().exists():
        raise RepoError("That does not look like a repository link: it contains spaces or control characters.")
    if spec.startswith("-"):
        raise RepoError("That does not look like a repository link.")
    return spec


def parse_source(spec: str) -> Source:
    spec = _clean(spec)
    local = Path(spec).expanduser()
    if local.exists() or re.match(r"^([A-Za-z]:[\\/]|[/~.]|\\\\)", spec):
        return Source("local", spec, path=str(local.resolve()) if local.exists() else str(local), name=local.name)
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", spec)
    if m:
        host, path = m.group(1), m.group(2)
        owner, _, name = path.rpartition("/")
        return Source("url", spec, url=f"git@{host}:{path}.git", host=host, owner=owner, name=name)
    if not re.match(r"^[a-z][a-z0-9+.-]*://", spec, re.I):
        if re.match(r"^[\w.-]+/[\w.-]+/?$", spec):                       # owner/repo
            spec = "https://github.com/" + spec.rstrip("/")
        elif re.match(r"^[\w.-]+\.[a-z]{2,}/", spec, re.I):              # github.com/owner/repo
            spec = "https://" + spec
        else:
            raise RepoError(f"'{spec}' is not a folder on this computer and not a repository link.")
    m = re.match(r"^(https?)://(?:[^@/]+@)?([^/]+)/(.+)$", spec, re.I)
    if not m:
        raise RepoError("Only https:// and ssh repository links are supported.")
    scheme, host, path = m.group(1).lower(), m.group(2).lower(), re.split(r"[?#]", m.group(3))[0].strip("/")
    parts = [p for p in path.split("/") if p]
    rest: list = []
    if "-" in parts and parts.index("-") + 1 < len(parts) and parts[parts.index("-") + 1] in ("tree", "blob"):   # GitLab
        i = parts.index("-"); rest = parts[i + 2:]; parts = parts[:i]
    elif len(parts) > 3 and parts[2] in ("tree", "blob", "commit", "commits", "pull", "issues", "releases", "actions", "wiki"):
        rest = parts[3:] if parts[2] in ("tree", "blob") else []; parts = parts[:2]
    elif len(parts) > 2 and host in ("github.com", "www.github.com"):
        parts = parts[:2]
    if len(parts) < 2:
        raise RepoError("The link must point at a repository, like https://github.com/owner/repo")
    parts[-1] = re.sub(r"\.git$", "", parts[-1])
    host = host.removeprefix("www.")
    return Source("url", spec, url=f"{scheme}://{host}/{'/'.join(parts)}.git", host=host, owner="/".join(parts[:-1]), name=parts[-1], rest=rest)


def friendly_git_error(text: str, host: str = "") -> str:
    t = text.lower()
    if "could not resolve host" in t or "unable to access" in t and "resolve" in t:
        return "Could not reach the server. Check the internet connection (or the VPN/proxy) and the host name in the link."
    if "not found" in t and ("repository" in t or "remote" in t) or "could not read username" in t or "authentication failed" in t or "terminal prompts disabled" in t or "permission denied (publickey)" in t:
        return ("The repository was not found, or it is private. For a private GitHub repository put GITHUB_TOKEN=<a token with read access> "
                "in the .env file; for ssh links make sure your ssh key works with this host.")
    if "remote branch" in t and "not found" in t:
        return "That branch does not exist in the repository."
    if "timed out" in t or "timeout" in t:
        return "The server did not answer in time. Try again."
    last = [l for l in text.strip().splitlines() if l.strip()][-1:] or ["unknown error"]
    return "git said: " + last[0][:300]


def _env() -> dict:
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "echo", "LC_ALL": "C"}


def _auth_args(src: Source) -> list[str]:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok and src.url.startswith("https://") and src.host in ("github.com",):
        b = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        return ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {b}"]
    return []


def _git(args: list[str], cwd=None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=_env(), capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def _with_auth_fallback(src: Source, run):
    """Try without credentials first (public repos must work even if a token in the environment has expired); then with the token."""
    try:
        return run([])
    except RepoError as first:
        auth = _auth_args(src)
        if not auth:
            raise
        try:
            return run(auth)
        except RepoError:
            raise first from None


def remote_info(src: Source) -> dict:
    """Ask the server (no download): does the repository exist, what is its default branch, and which branches exist?"""
    def run(auth):
        try:
            r = _git([*auth, "ls-remote", "--symref", src.url, "HEAD", "refs/heads/*"], timeout=60)
        except subprocess.TimeoutExpired:
            raise RepoError(friendly_git_error("timed out")) from None
        except FileNotFoundError:
            raise RepoError("git is not installed on this computer.") from None
        if r.returncode != 0:
            raise RepoError(friendly_git_error(r.stderr or r.stdout, src.host))
        default, heads = "", []
        for line in r.stdout.splitlines():
            if line.startswith("ref: "):
                default = line.split()[1].removeprefix("refs/heads/")
            elif "\trefs/heads/" in line:
                heads.append(line.split("\trefs/heads/", 1)[1])
        return {"default": default, "heads": heads, "auth": bool(auth)}
    return _with_auth_fallback(src, run)


def pick_branch(src: Source, info: dict) -> tuple[str, list]:
    """For /tree/<branch>/<folder> links: the longest prefix that is a real branch (branch names may contain slashes)."""
    for i in range(len(src.rest), 0, -1):
        cand = "/".join(src.rest[:i])
        if cand in info["heads"]:
            return cand, src.rest[i:]
    if src.rest:
        raise RepoError(f"Branch '{src.rest[0]}' from the link does not exist. Available: {', '.join(info['heads'][:6]) or 'none'}")
    return info["default"], []


def _same_origin(dest: Path, src: Source) -> bool:
    r = _git(["-C", str(dest), "config", "--get", "remote.origin.url"], timeout=20)
    norm = lambda u: re.sub(r"(\.git)?/?$", "", u.strip().lower().replace("git@", "https://").replace(":", "/", 1) if u.startswith("git@") else u.strip().lower())
    return r.returncode == 0 and norm(r.stdout) == norm(src.url)


def destination(src: Source, workdir: Path) -> Path:
    workdir = Path(workdir)
    for name in (src.name, f"{src.owner.replace('/', '-')}-{src.name}"):
        d = workdir / name
        if not d.exists() or (d / ".git").exists() and _same_origin(d, src):
            return d
    return workdir / f"{src.owner.replace('/', '-')}-{src.name}-{int(time.time())}"


def _stream(cmd: list[str], log: Callable[[str], None], progress: Callable[[str, int], None] | None, timeout: int = 900) -> tuple[int, str]:
    """Run git and read its progress ("Receiving objects:  45%") as it is printed."""
    p = subprocess.Popen(cmd, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, bufsize=0)
    buf, text, last, start = "", "", "", time.time()
    while True:
        ch = p.stdout.read(1)
        if ch == "" and p.poll() is not None:
            break
        if time.time() - start > timeout:
            p.kill(); text += "\ntimed out"; break
        if ch in ("\r", "\n", ""):
            line = buf.strip(); buf = ""
            if line:
                text += line + "\n"
                m = re.match(r"^(Counting objects|Compressing objects|Receiving objects|Resolving deltas|remote: Counting objects|remote: Compressing objects):\s+(\d+)%", line)
                if m and progress:
                    progress(m.group(1).replace("remote: ", ""), int(m.group(2)))
                elif line != last and not m:
                    log("  " + re.sub(r"(AUTHORIZATION: basic )\S+", r"\1***", line)[:200])
                last = line
        else:
            buf += ch
    p.wait()
    return p.returncode, text


def ingest(spec: str, workdir: Path, log: Callable[[str], None] = print, progress: Callable[[str, int], None] | None = None) -> tuple[Path, Source, bool]:
    """Resolve the link, check it exists, clone (or update) it. Returns (folder, source, newly_cloned)."""
    src = parse_source(spec)
    if src.kind == "local":
        p = Path(src.path)
        if not p.is_dir():
            raise RepoError(f"{p} is not a folder.")
        if not (p / ".git").exists():
            raise RepoError(f"{p} is not a git repository. Run `git init` and make one commit there, or paste its GitHub link instead.")
        log(f"Using the local repository {p}")
        return p, src, False
    log(f"Repository: {src.host}/{src.owner}/{src.name}")
    info = remote_info(src)
    auth = _auth_args(src) if info["auth"] else []
    branch, folder = pick_branch(src, info)
    src.branch = branch
    log(f"Found it. Branch: {branch or '(default)'}" + (f" (the folder part of the link, {'/'.join(folder)}, is ignored: the whole repository is analysed)" if folder else ""))
    dest = destination(src, workdir)
    if (dest / ".git").exists():
        log(f"Already cloned at {dest}; fetching the latest.")
        r = _git([*auth, "-C", str(dest), "fetch", "--depth", str(DEPTH), "origin", branch or "HEAD"], timeout=300)
        dirty = _git(["-C", str(dest), "status", "--porcelain", "--", ".", ":(exclude).evolution"]).stdout.strip()
        cur = _git(["-C", str(dest), "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        if r.returncode == 0 and not dirty and cur == (branch or cur):
            _git(["-C", str(dest), "merge", "--ff-only", "FETCH_HEAD"])
        elif dirty:
            log("  It has uncommitted changes, so it was not updated.")
        _finish(dest, src)
        return dest, src, False
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", *auth, "clone", "--progress", "--depth", str(DEPTH), "--single-branch"] + (["--branch", branch] if branch else []) + [src.url, str(dest)]
    log(f"Cloning into {dest} (the most recent {DEPTH} commits)...")
    code, text = _stream(cmd, log, progress)
    if code != 0:
        raise RepoError(friendly_git_error(text, src.host))
    _finish(dest, src)
    return dest, src, True


def _finish(dest: Path, src: Source) -> None:
    """Make sure commits can be made here, and remember where this copy came from."""
    for key, val in (("user.name", "Evolve"), ("user.email", "evolve@localhost")):
        if not _git(["-C", str(dest), "config", key]).stdout.strip():
            _git(["-C", str(dest), "config", key, val])
    if not _git(["-C", str(dest), "rev-parse", "HEAD"]).stdout.strip():
        raise RepoError("The repository has no commits yet, so there is nothing to analyse.")
    meta = dest / ".evolution"
    meta.mkdir(exist_ok=True)
    (meta / "source.json").write_text(__import__("json").dumps({"url": src.url, "branch": src.branch, "cloned": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1), encoding="utf-8")


def describe(repo: Path) -> dict:
    """A short summary of a working copy for the dashboard."""
    repo = Path(repo)
    g = lambda *a: _git(["-C", str(repo), *a]).stdout.strip()
    files = [f for f in g("ls-files").splitlines() if f and not f.startswith(".evolution")]
    langs = collections.Counter(LANGS[Path(f).suffix.lower()] for f in files if Path(f).suffix.lower() in LANGS)
    src = {}
    sp = repo / ".evolution" / "source.json"
    if sp.exists():
        try:
            src = __import__("json").loads(sp.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"name": repo.name, "branch": g("rev-parse", "--abbrev-ref", "HEAD"), "commit": g("rev-parse", "--short", "HEAD"), "message": g("log", "-1", "--pretty=%s")[:90],
            "date": g("log", "-1", "--pretty=%cs"), "files": len(files), "languages": [{"name": k, "files": v} for k, v in langs.most_common(4)], "url": src.get("url", ""),
            "commits": int(g("rev-list", "--count", "HEAD") or 0)}
