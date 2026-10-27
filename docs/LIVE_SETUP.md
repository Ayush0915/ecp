# Live setup: your mentor's machine (Azure OpenAI + Podman)

Goal: hand over any repo, and watch it evolve under the ECP process with **GPT-5.5 on Azure** and **Pi running inside Podman**.

## Ubuntu on WSL (your setup)

- Run everything inside the Ubuntu terminal. Keep the repo under your Linux home (`~/work/...`), not under `/mnt/c`, so Podman mounts are fast and permissions work.
- Install: `sudo apt install podman git python3-venv`. Rootless Podman works on recent WSL2; if `podman info` complains, run `evolve doctor` and read the line it prints.
- Start the dashboard in Ubuntu (`evolve dashboard`) and open **http://localhost:8765** in the Windows browser (WSL forwards localhost).

## What is needed on that machine

Python 3.10+, git, Node is NOT needed on the host (Pi lives in the container), Podman, an Azure OpenAI GPT-5.5 deployment.

## One-time setup (about 10 minutes)

```bash
# 1. Install
cd ecp-governed-evolution
pip install -e .

# 2. Azure credentials (names fixed; values are yours)
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
# PowerShell:  $env:AZURE_OPENAI_API_KEY="..."

# 3. Build the box Pi works in
evolve sandbox build

# 4. Give it the repo and check readiness
cd <the-repo>                         # must be a git repo with tests, clean working tree
evolve init --profile azure-podman
evolve doctor --ping                  # lists anything missing; --ping makes one real GPT-5.5 call
```

`doctor` must say **Ready.** before the demo. If your Azure *deployment name* is not `gpt-5.5`, edit
`.evolution/config.yaml`: `models.elephant/goldfish/correctness` (deployment names) and `pi.model` (`azure/<model>`);
if Pi's model name differs from the deployment name, also set `AZURE_OPENAI_DEPLOYMENT_NAME_MAP=gpt-5.5=<deployment>`.
Check Pi's model list with: `podman run --rm <image> --list-models azure`.

Then open `.evolution/rules.md` and write the repo's rules in plain sentences (this is what Goldfish enforces).
Check `test_command` in `.evolution/config.yaml` and that the tests pass before any change.

## Optional: a second model for the correctness reviewer

If the Azure portal (Deployments page) lists another model, put its deployment name in `.evolution/config.yaml` under
`models.correctness`. Leave it empty to reuse the same deployment (a weaker check, say so in the demo).

## Run a change

```bash
evolve run "Bug: <describe it>" --open-report
# or hand it a repo URL (it clones to ./workspaces/<name> and sets itself up):
evolve run "Add input validation to the signup endpoint" --repo https://github.com/org/repo --profile azure-podman --open-report
```

What you will see in the terminal, in order: Elephant drafts the ECP -> Goldfish review rounds with findings -> plan locked
-> Pi builds in the container (blocked edits shown) -> tests and scope check -> post-review -> ACCEPTED or ROLLED BACK.
At the end a report opens: `.evolution/reports/ECP-000N.html` (plan, findings, guard blocks, diff, evidence trail). Regenerate any time with `evolve report`.

High-risk plans (or anything touching `protected_paths`) pause and ask: "Approve? [y/N]". That is the human in control.

## Show it on the dashboard

```bash
evolve dashboard          # open http://localhost:8765, type the request, press Run governed evolution
```

High-risk approvals appear as an **Approve / Decline** banner on the page. The page also has **Compare: plain vs governed**.
See `docs/DEMO_SCRIPT.md` for what to say.

## What the container can and cannot do

- Sees only: the repo (read-write), the guard (read-only), a small run folder.
- No extra Linux powers (`--cap-drop=ALL`, `no-new-privileges`), no shell tool for Pi, API keys passed by name only.
- Pi's file edits go through the guard, then the diff is re-checked outside the container.
- Network is left on, because Pi must reach Azure.

## Rehearse first (no internet needed)

`evolve demo all` (or `evolve dashboard --demo`) replays the scripted scenarios. Do this on the mentor's machine before the live run.

## Demo order that works

1. `evolve demo 1_flawed_plan_caught` (2 minutes): shows review improving a plan.
2. Live run on the mentor's repo with a real bug or small feature request.
3. Open the report; point at Goldfish findings, the guard log, the evidence chain.
4. Optional: ask for something risky (touch a protected path) to show the human gate.

## If something fails

| Symptom | Likely cause |
|---|---|
| `doctor` says image missing | run `evolve sandbox build` |
| Model call fails | wrong endpoint or deployment name; `evolve doctor --ping` prints the Azure error |
| Pi starts but "No API key" / model not found | model id in `pi.model`; check `--list-models` |
| "repo has uncommitted changes" | commit or stash first |
| Everything rolled back, "Pi made no changes" | Pi could not reach the model or edit; read `.evolution/evidence/ECP-000N.jsonl` (`pi_error`) |
| Windows path/mount errors | start `podman machine` and keep the repo under your user folder |
