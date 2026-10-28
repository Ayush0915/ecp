# Running on the office laptop (Windows, PowerShell)

Follow the steps in order. Each step says what you should see. If a step does not match, go to Troubleshooting at the end.

## 0. What you need first

Check each of these in PowerShell:

```powershell
python --version      # 3.10 or newer
git --version
node --version        # 20 or newer
npm --version
```

You also need three Azure values from your team: the resource URL, the chat deployment name, and either an API key or a service principal (tenant id, client id, client secret).

## 1. Get the code

```powershell
cd D:\
mkdir work -ErrorAction SilentlyContinue
cd work
git clone https://github.com/Ayush0915/ecp.git
cd ecp
```

## 2. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
npm install -g @earendil-works/pi-coding-agent
pi --version
```

If `Activate.ps1` is blocked, run this once and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

If the office network uses a proxy, set it before installing and cloning:

```powershell
$env:HTTPS_PROXY = "http://proxy-host:port"
$env:HTTP_PROXY  = "http://proxy-host:port"
git config --global http.proxy http://proxy-host:port
npm config set proxy http://proxy-host:port
npm config set https-proxy http://proxy-host:port
```

## 3. Azure settings

```powershell
copy .env.example .env
notepad .env
```

Fill in the file. Use the key, or leave the key empty and fill the three service principal lines:

```
AZURE_OPENAI_BASE=https://YOUR-RESOURCE.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=YOUR-CHAT-DEPLOYMENT
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_API_KEY=

AZURE_OPENAI_TENANTID=
AZURE_OPENAI_CLIENTID=
AZURE_OPENAI_CLIENTSECRET=
```

Rules: no quotes, no spaces around `=`, the base URL has no `/openai` at the end. `.env` is ignored by git, so it is never committed.

You can also enter these later in the page under Settings.

## 4. Check the machine

```powershell
evolve doctor --ping --skip-tests
```

You should see a tick for each line: git, Pi, Azure base, deployment, credentials, API version, and one real model call.
If the model call fails, the message says why (see Troubleshooting).

## 5. Start the app

```powershell
evolve start
```

Open http://127.0.0.1:8765/ if it does not open by itself. To use another port: `evolve start --port 8800`.

## 6. Use it

1. Paste `https://github.com/dbader/schedule` and press Connect. Watch: link check, download, private environment, dependencies, baseline tests.
2. Press Discover problems. Findings appear with evidence bars.
3. Press Evolve this on one finding. Watch the Elephant write the ECP as YAML, the Goldfish reviewers, Pi's file actions, the checks and the decision.
4. Open a finished run from Previous runs to replay it.

Cloned repositories are stored in the `workspaces` folder next to the app. Real files for a run are in `workspaces\<repo>\.evolution\`:

```powershell
dir workspaces\schedule\.evolution
type workspaces\schedule\.evolution\ecps\ECP-0001.md
type workspaces\schedule\.evolution\evidence\ECP-0001.jsonl
```

## 7. Other repositories

- Private GitHub repository: add `GITHUB_TOKEN=...` (read access) to `.env`, restart `evolve start`, paste the link.
- A branch: paste `https://github.com/owner/repo/tree/branch-name`.
- A folder already on the laptop: paste its path. It must be a git repository with no uncommitted changes.
- The repository needs tests. Without tests nothing can be verified.

## 8. Same thing from the terminal

```powershell
evolve prepare  --repo https://github.com/dbader/schedule
evolve discover --repo workspaces\schedule
evolve run --repo workspaces\schedule --finding F-001
evolve show ECP-0001 --repo workspaces\schedule
```

## 9. Podman (optional, later)

Only if Podman is installed. It makes Pi run in a container that sees only the repository.

```powershell
evolve sandbox build
```

Then choose Podman under Settings, This repository. Skip this for the first run.

## 10. Updating later

```powershell
cd D:\work\ecp
git pull
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Troubleshooting

| What you see | Cause and fix |
|---|---|
| `evolve` is not recognized | The virtual environment is not active. Run `.\.venv\Scripts\Activate.ps1`, or use `python -m evolve start`. |
| `doctor`: base URL missing or wrong | `AZURE_OPENAI_BASE` must look like `https://name.openai.azure.com`, no trailing path. |
| Azure 401 | Wrong key, or the service principal has no access. The principal needs the role Cognitive Services OpenAI User on the resource. |
| Azure 403 | Network rule on the resource blocks this laptop, or a missing role. Ask the Azure owner. |
| Azure 404 | The deployment name is wrong. Use the deployment name, not the model name. |
| Azure 429 | Rate limit. The app retries. If it keeps happening, ask for a higher quota. |
| Token request fails | The laptop cannot reach `login.microsoftonline.com`. Check the proxy. |
| Clone fails with "not found" | The link is wrong, or the repository is private and `GITHUB_TOKEN` is not set. |
| Clone hangs or "could not resolve host" | Proxy not set for git. See step 2. |
| `pi` not found | Run `npm install -g @earendil-works/pi-coding-agent` again, then open a new PowerShell window. |
| Baseline tests fail | In the page, open "Why the baseline fails". Usually a missing dependency or a test that needs a database. Fix it, then press Reinstall dependencies. |
| "The repo has uncommitted changes" | Commit or stash changes in that repository. A rollback needs a clean tree. |
| Port 8765 in use | `evolve start --port 8800` |

If something else fails, copy the last 30 lines from the PowerShell window and the text under "Raw output" in the page.
