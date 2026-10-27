# Using it on any repo

## What your repo needs

1. **Git** (rollback relies on branches).
2. **Tests that cover the code being changed.** The tests are the judge. No tests = no safe verification.
3. A clean working tree when you start (commit or stash first).

## Setup

```bash
pip install -e /path/to/ecp-governed-evolution
cd your-repo
evolve init
```

`init` creates `.evolution/` with `config.yaml` (auto-detects pytest, npm, go or cargo), `rules.md` and folders for ECPs and evidence.
Open **`.evolution/rules.md`** and write your team's rules in plain sentences. Both Elephant and Goldfish read it.

## Config (`.evolution/config.yaml`)

| Setting | Meaning |
|---|---|
| `test_command` | How to run your tests |
| `protected_paths` | Files that force a human approval when a plan touches them (e.g. `auth/**`, `migrations/**`) |
| `models.provider` | `azure` (Azure OpenAI), `anthropic`, `openai` (any OpenAI-compatible API, e.g. Groq) or `mock` |
| `models.elephant / goldfish / correctness` | Model names. Use a *different* model for `correctness` than the one that builds |
| `models.api_key_env` | Name of the environment variable holding your key |
| `pi.backend` | `pi` for the real Pi agent, `scripted` for demos |
| `pi.model` | Model Pi should use |
| `pi.allow_bash` | Leave `false` unless you really need it |
| `max_revise_rounds` | How many plan revisions before a human takes over (default 3) |

## Run a change

```bash
export ANTHROPIC_API_KEY=...
evolve run "Bug: login accepts an empty password"
```

Install Pi first for real building: `npm i -g @earendil-works/pi-coding-agent` (Node 22+), and give Pi its own model key.
Review a hand-written plan only: `evolve review path/to/ECP-0001.md --repo .`
See history: `evolve status`. See one change in full: `evolve show ECP-0001`.

## Safety notes

- Pi can run code, and Pi has no built-in permission system. The guard limits *file edits*; for untrusted work, run everything inside a container or VM.
- Rollback keeps the `.evolution/` records on purpose. They are the memory.
