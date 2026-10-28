# What is tested

Run everything: `pytest -q` (about 100 tests, ~1.5 minutes). Also: `node guard/test-core.mjs` and `node guard/test-hook.mjs`.

| Area | How it is tested |
|---|---|
| ECP file format, completeness check | round-trip and bad-input tests |
| Plan lock and tamper detection | change plan after lock -> detected; status change -> lock still valid |
| Evidence chain | edit one line -> chain reports broken |
| Lifecycle | cannot skip steps; cannot approve incomplete ECP; cannot run after plan was changed |
| Scope rules | 17 shared cases (`tests/scope_cases.json`) run against **both** the Python rules and the JS guard rules |
| Guard extension | `guard.ts` is loaded the way Pi loads it; out-of-scope writes, protected paths and bash are blocked, in-scope edits pass, every attempt is logged |
| Goldfish | right reviewers per change type; correctness uses a different model; **reviewers never see the request or Elephant's reasoning**; bad reviewer output fails closed |
| Risk gate | low is automatic, high asks a human, protected paths force high |
| Verify | out-of-scope change is caught even if the guard missed it |
| Whole pipeline (real git, real tests) | flawed plan caught and fixed; out-of-scope edits blocked; broken change rolled back and repo identical; high-risk declined -> never executed; stuck plan rejected after max rounds; dirty repo refused; green tests + correctness blocker -> rolled back; history carries to the next plan |

| Fail-before / pass-after | bug fix with no test is rolled back; a test that passes on the old code is rejected as proof; real fix with real test is "proved"; tree is restored after the check; enhancements skip it |
| Plan versions | every revision is saved; v1 vs v2 differ as expected |
| Reviewer says "insufficient context" | counts as a blocking finding and sends the plan back |
| Triggers | failing tests become a request; dashboard approval handshake works |
| Dashboard (real HTTP) | page served; run started from the page; one run at a time; approve and decline high-risk; Azure settings saved to `.env` with secrets never returned; connecting a repository; evidence chain and versions served; file reader cannot leave `.evolution/` |
| Guard path handling | Pi may pass absolute paths: inside the repo = understood, outside / `..` / `~` = blocked |
| Doctor | flags missing Azure variables, failing baseline tests, dirty tree, unset repo |
| Azure details | Pi's variable names accepted, URL tidied, rate limits retried, hard errors readable |
| Live paths (stand-ins) | Azure client against a fake Azure server (URL, api-version, key or bearer token, token endpoint, service principal); Pi provider file; exact Podman command (3 mounts only, no capabilities, no bash, no secrets in the command); missing-podman message |
| URL ingestion and cloning | 11 link forms parsed; option injection and bad input refused; branch with slashes; a real clone from a local stand-in remote, then fetch instead of re-clone; missing remote and non-git folder give plain errors |

## Found and fixed while testing

- Pi may hand the guard absolute paths. Without conversion every legitimate edit would have been blocked. Fixed in `guard/guard.ts`, covered by `guard/test-hook.mjs`.
- File lists from git were losing their first character in the comparison feature (a trimming bug). Found by a test, fixed.
- Committing records failed silently when a records folder was empty. Fixed.

- A one-item JSON list from a reviewer was read as a single object, silently dropping the finding. Fixed in `llm.extract_json`.

## NOT tested yet (needs API keys / live services)

- A real call to Azure OpenAI (only a fake server was used). `evolve doctor --ping` tests this on the target machine.
- Podman itself (not installed here): only the command line is tested. `evolve sandbox build` and one real run are the proof on the target machine.
- The real Pi agent doing a full run with a model (Pi starts and loads the guard; no API key was available for a model run).
- Quality of real Goldfish reviews: the lens prompts are drafts and need tuning against real models.
- The dashboard was tested in a headless browser and over HTTP here, not yet on your Ubuntu/WSL machine (open it from the Windows browser via localhost).
- Proof tests for blockers (not built).
- The fail-before check proves new tests fail on old code; it cannot tell *why* they fail (a broken import would also count). Reviewers and humans cover that gap.

## Live-path tests
- `tests/test_live_e2e.py` runs the real Azure client over HTTP and the real Pi agent with the real guard. Only the model's answers come from `tests/fake_azure.py`. It proves discovery finds a problem, the flow evolves it, the guard blocks an out-of-scope write made by real Pi, and the finding is marked accepted. Needs Pi installed (`PI_BIN=/path/to/pi`).
- `tests/test_discover.py`, `tests/test_prepare.py`, `tests/test_dashboard.py`, `tests/test_repo.py` cover discovery, environment preparation, the dashboard API and URL ingestion.
- Checked by hand: cloning and preparing a real public repository (dbader/schedule).

## Still NOT tested (needs a real Azure resource)
- A real Azure deployment answering as Elephant, Goldfish and Pi, including key and Entra token authentication and Pi's `/openai/v1` route.
- Quality of real plans, findings and edits.
- Pi's behaviour with every model.
