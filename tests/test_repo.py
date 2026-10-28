"""URL ingestion and cloning (no network: a local bare repository stands in for the remote)."""
import subprocess

import pytest

from evolve import repo as R


def sh(*a, cwd=None):
    subprocess.run(a, cwd=cwd, check=True, capture_output=True)


@pytest.mark.parametrize("spec,owner,name,branch", [
    ("https://github.com/dbader/schedule", "dbader", "schedule", ""),
    ("https://github.com/dbader/schedule.git", "dbader", "schedule", ""),
    ("https://github.com/dbader/schedule/", "dbader", "schedule", ""),
    ("github.com/dbader/schedule", "dbader", "schedule", ""),
    ("dbader/schedule", "dbader", "schedule", ""),
    ("git@github.com:dbader/schedule.git", "dbader", "schedule", ""),
    ("ssh://git@github.com/dbader/schedule.git", "dbader", "schedule", ""),
    ("https://github.com/dbader/schedule/tree/main", "dbader", "schedule", "main"),
    ("https://github.com/dbader/schedule/blob/main/schedule/__init__.py", "dbader", "schedule", "main"),
    ("https://github.com/dbader/schedule/pull/12", "dbader", "schedule", ""),
    ("https://gitlab.com/g/p/-/tree/dev", "g", "p", "dev"),
])
def test_links_are_understood(spec, owner, name, branch):
    s = R.parse_source(spec)
    assert (s.owner, s.name) == (owner, name)
    assert s.kind == "url" and s.url.startswith(("https://", "git@", "ssh://")) and "token" not in s.url
    if branch:
        assert s.rest and s.rest[0] == branch


@pytest.mark.parametrize("spec", ["", "   ", "-oProxyCommand=x", "--upload-pack=x", "two words", "https://github.com/onlyowner"])
def test_bad_input_is_refused_with_a_message(spec):
    with pytest.raises(R.RepoError):
        R.parse_source(spec)


def test_branch_is_the_longest_real_prefix():
    s = R.parse_source("https://github.com/o/r/tree/feature/x/src/app")
    branch, folder = R.pick_branch(s, {"default": "main", "heads": ["main", "feature/x"]})
    assert (branch, folder) == ("feature/x", ["src", "app"])


def test_git_errors_are_translated():
    msg = R.friendly_git_error("fatal: could not read Username for 'https://github.com': terminal prompts disabled", "github.com")
    assert "GITHUB_TOKEN" in msg


def test_clone_a_remote_then_reuse_it(tmp_path, monkeypatch):
    work = tmp_path / "w"
    work.mkdir()
    sh("git", "init", "-q", "-b", "main", str(work))
    sh("git", "-C", str(work), "config", "user.email", "a@b.c")
    sh("git", "-C", str(work), "config", "user.name", "t")
    (work / "app.py").write_text("def f():\n    return 1\n")
    sh("git", "-C", str(work), "add", ".")
    sh("git", "-C", str(work), "commit", "-qm", "first")
    bare = tmp_path / "remote.git"
    sh("git", "clone", "-q", "--bare", str(work), str(bare))

    # make git treat the bare folder as if it were this GitHub address, so the whole URL path runs without a network
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{bare}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/acme/widget.git")
    link = "https://github.com/acme/widget"
    ws, lines, pct = tmp_path / "ws", [], []
    path, src, fresh = R.ingest(link, ws, log=lines.append, progress=lambda s, p: pct.append(p))
    assert fresh and (path / "app.py").exists() and (path / ".evolution" / "source.json").exists()
    info = R.describe(path)
    assert info["branch"] == "main" and info["files"] >= 1 and info["languages"][0]["name"] == "Python" and info["commits"] == 1

    (work / "app.py").write_text("def f():\n    return 2\n")
    sh("git", "-C", str(work), "commit", "-qam", "second")
    sh("git", "-C", str(work), "push", "-q", str(bare), "main")
    path2, _, fresh2 = R.ingest(link, ws, log=lines.append)
    assert path2 == path and not fresh2 and R.describe(path2)["commits"] == 2   # fetched, not re-cloned


def test_missing_remote_gives_a_plain_message(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{tmp_path}/nothing.git.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "https://github.com/acme/missing.git")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    with pytest.raises(R.RepoError):
        R.ingest("https://github.com/acme/missing", tmp_path / "ws", log=lambda s: None)


def test_local_folder_must_be_a_git_repo(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(R.RepoError):
        R.ingest(str(tmp_path / "plain"), tmp_path / "ws", log=lambda s: None)
