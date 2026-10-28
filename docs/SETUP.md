# Setup

## 1. Install

```bash
pip install -e ".[dev]"                         # the app, plus bandit, ruff, coverage, pytest for discovery
npm i -g @earendil-works/pi-coding-agent        # Pi, the coding agent
```

Requires Python 3.10+, git and Node 20+.

## 2. Azure OpenAI

Copy `.env.example` to `.env` in the project folder and fill it in. The same values can be entered under Settings in the page.

| Variable | Meaning |
|---|---|
| `AZURE_OPENAI_BASE` | `https://YOUR-RESOURCE.openai.azure.com` |
| `AZURE_OPENAI_DEPLOYMENT` | The name of your chat deployment |
| `AZURE_OPENAI_API_VERSION` | Defaults to `2024-10-21` |
| `AZURE_OPENAI_API_KEY` | Key authentication |
| `AZURE_OPENAI_TENANTID`, `AZURE_OPENAI_CLIENTID`, `AZURE_OPENAI_CLIENTSECRET` | Service principal, used when the key is empty |
| `GITHUB_TOKEN` | Optional. Only for private GitHub repositories |

Calls go to `{BASE}/openai/deployments/{DEPLOYMENT}/chat/completions?api-version={VERSION}`. With a service principal a
token is requested from Microsoft Entra for the scope `https://cognitiveservices.azure.com/.default` and reused until it
is about to expire. Pi is pointed at the same deployment through a private provider file that exists only while Pi runs.

`evolve doctor --ping` makes one tiny real call and reports what is wrong.

Optional: use a different deployment per role (Elephant, Goldfish, the correctness reviewer) under
Settings, This repository. An empty field means `AZURE_OPENAI_DEPLOYMENT`.

## 3. Start and connect a repository

```bash
evolve start
```

![Paste a link](docs/images/01-connect.png)

Accepted forms:

| You paste | Result |
|---|---|
| `https://github.com/owner/repo` | The default branch |
| `https://github.com/owner/repo.git` | Same |
| `https://github.com/owner/repo/tree/branch` | That branch |
| `https://github.com/owner/repo/tree/branch/folder` | That branch (branch names with slashes work) |
| `https://github.com/owner/repo/blob/...`, `/pull/N` | The repository the link belongs to |
| `owner/repo`, `github.com/owner/repo` | GitHub shorthand |
| `git@github.com:owner/repo.git`, `ssh://git@host/owner/repo.git` | Over SSH, using your keys |
| GitLab links, including `/-/tree/branch` | Same as GitHub |
| A folder path | Used where it is. It must already be a git repository |

What happens, in order:

1. The link is checked. Anything that looks like a command option is refused.
2. `git ls-remote` asks the server whether the repository and branch exist, without downloading.
3. A shallow clone (recent 500 commits, one branch) goes into `workspaces/`, with a progress bar.
4. A private virtual environment is created outside the repository and its dependencies are installed.
5. The repository's tests run once to record a baseline.

![Getting the repository ready](docs/images/00-clone-progress.png)

If the same repository is pasted again it is fetched and fast-forwarded, not cloned twice.

Private repositories: the first attempt is anonymous. If git is refused and `GITHUB_TOKEN` is set, it retries with the
token sent as a header for that one command. The token is never written into the repository, its remote URL or the logs.

Errors are translated: repository not found, private without a token, bad branch, no network, git not installed.

## 4. Podman (optional)

```bash
evolve sandbox build
```

Then choose Podman under Settings, This repository. Pi runs in a container that sees only the repository and a run folder.
