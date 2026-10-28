"""Environment preparation: only the plan is tested here (no installs)."""
from evolve import prepare as P


def test_install_commands_follow_the_repo_layout(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests\n")
    (tmp_path / "requirements-dev.txt").write_text("pytest\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    cmds = [" ".join(c[3:]) for c in P.install_commands(tmp_path, "py")]
    assert cmds == ["install -q -r requirements.txt", "install -q -r requirements-dev.txt", "install -q -e ."]


def test_repo_kinds(tmp_path):
    assert not P.is_python_repo(tmp_path)
    (tmp_path / "tests").mkdir()
    assert P.is_python_repo(tmp_path)


def test_env_lives_outside_the_repo(tmp_path):
    assert tmp_path not in P.env_dir(tmp_path).parents and P.env_dir(tmp_path).name == tmp_path.name


def test_requirement_lines_skip_comments_and_options(tmp_path):
    f = tmp_path / "r.txt"
    f.write_text("# c\n-r other.txt\nrequests==2.0  # pin\n\nblack\n--index-url x\n")
    assert P.requirement_lines(f) == ["requests==2.0", "black"]


def test_failure_detail_keeps_the_explaining_lines():
    out = "collecting\nE   ModuleNotFoundError: No module named 'yaml'\n1 error in 0.1s\n"
    assert "No module named" in P.failure_detail(out)
    assert P.MISSING.search(out).group(1) == "yaml" and P.PIP_NAMES["yaml"] == "pyyaml"


def test_env_file_in_utf16_is_read(tmp_path, monkeypatch):
    from evolve.cli import load_env_files
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("AZURE_OPENAI_API_KEY=test-key \r\n", encoding="utf-16")
    monkeypatch.chdir(tmp_path)
    load_env_files()
    import os
    assert os.environ.get("AZURE_OPENAI_API_KEY") == "test-key"
