# Required changes

> **Scope note (30 Oct 2026):** the work is now limited to correcting the core design, see [CORE_CORRECTIONS.md](CORE_CORRECTIONS.md). Items here that only add capability are parked.

Every change from [ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md), as one checklist. Baseline: commit `16e2ec1`, 115 tests passing.
Each row says what to change, where, and how to tell it is done. "Needs" lists the changes that must land first.
None of these change the core: the ECP, Elephant (plans, never codes), Goldfish (reviewers with no context), Pi (builds inside an enforced scope) and a control plane with no AI.

Rule for every row: add or extend a test that fails before the change and passes after it.

---

## Phase 1: P0 bugs and safety holes (before the demo)

| ID | Change | Files | Done when | Needs |
|---|---|---|---|---|
| [x] C01 | Wrap every step after `start_branch` in `try/except/finally`. On any exception: `decide.rollback`, outcome `aborted`, evidence entry `{kind:"abort", error, traceback_tail}`, then `_finish`. Add `aborted` as an allowed outcome | `engine.py`, `lifecycle.py` | An exception thrown in verify or the post-change review leaves the repo on the base branch with a clean tree, the ECP `decided`, and an `abort` line in the evidence | none |
| [x] C02 | Goldfish `one()`: catch the exception and return `[Finding(severity="major", claim="REVIEWER UNAVAILABLE: …")]` instead of `raise` | `goldfish/runner.py:144` | One lens raising does not crash the run; the run gets a blocking finding | none |
| [x] C03 | `_parse`: accept a JSON list, or a dict **containing** `findings`. Any other shape is a fail-closed major finding | `goldfish/runner.py:116` | `{"issues":[...]}` and `{}` produce a blocking finding, not `[]` | none |
| [x] C04 | Fail-before proof: (a) run **only** the new or changed test files (pytest node IDs) on the old code; (b) count it as proved only if they fail on **assertions** (collection/ImportError means `not_proved`, read from `--junitxml`); (c) the old code without the new tests must match the baseline | `verify.py:83-111` | Fix adds `utils/new.py` and the test imports it, so the result is `not_proved`. A test already failing at baseline does not count as proof | none |
| [x] C05 | Dashboard POST guard: require `Content-Type: application/json`, an `Origin`/`Host` of `127.0.0.1:<port>` or `localhost:<port>`, and a header `X-Evolve-Token` matching a random token made at startup and embedded in `index.html`. Do the same `Host` check on GET (DNS rebinding) | `dashboard/server.py`, `index.html` | A `text/plain` form POST from another origin gets 403; the page still works | none |
| [x] C06 | On connect or init, if the cloned repo ships `.evolution/config.yaml` or `rules.md`, do not use them. Move them to `.evolution/untrusted/`, write fresh defaults, and show a warning in the dashboard | `dashboard/server.py:215`, `config.py:init_repo`, `cli.py` | A fixture repo with a malicious `test_command` in its config never runs that command | none (C11 replaces this later) |
| [x] C07 | Run an ECP `verification[].command` only if it matches an allowlist (`pytest`, `python -m pytest`, `"<venv-python>" -m pytest`, `npm test`, `go test`, `cargo test`, plus args). Anything else fails as the check `command not allowed`. Use `shlex` and no `shell=True` for these commands | `verify.py:47`, `config.py` (allowlist setting) | `curl x \| sh` in a check fails without running | none |
| [x] C08 | Record the approval mode truthfully. `gate.decide` returns `auto (low risk)`, `auto-yes (--yes flag)`, `human approved via terminal`, or `human approved via dashboard` | `gate.py:37`, `cli.py:_ask_factory` | A run with `--yes` shows `auto-yes` in the evidence | none |
| [x] C09 | Add `encoding="utf-8"` to every bare `read_text()` | `elephant.py:65`, `cli.py:39,223`, `dashboard/server.py:63`, `discover.py:163` | `grep -rn "read_text()" evolve` is empty; a test with a non-ASCII ECP title passes | none |
| [x] C10 | Remove auto-install from the "No module named X" output, or install only names declared in the repo's requirements files or pyproject | `prepare.py:157-162` | Test output naming an undeclared module installs nothing | none |

## Phase 2: Governance (controlled autonomy as configuration)

| ID | Change | Files | Done when | Needs |
|---|---|---|---|---|
| [x] C11 | **Governance store outside the target repo**: `~/.evolve/repos/<owner-name-hash>/` holds `config.yaml`, `rules.md`, `governance.yaml`. `load_config` reads from there; `.evolution/` in the repo keeps records only | new `evolve/store.py`, `config.py`, `cli.py`, `dashboard/server.py`, docs | Deleting the repo's `.evolution/config.yaml` changes nothing; the dashboard settings edit the store | C06 |
| [x] C12 | **`governance.yaml` model** (pydantic): `autonomy_level` (L0–L3), `allowed_change_types`, `max_files`, `max_diff_lines`, `required_lenses` per type, `human_gates` (plan/result), `merge_mode`, `token_budget` | new `evolve/governance.py` | Loads with defaults; invalid values give a readable error | C11 |
| [x] C13 | **Enforce autonomy levels** in the engine. L0 = discover only; L1 = human approves every plan and result; L2 = low risk runs on its own; L3 = L2 plus picking its own finding (C62) | `engine.py`, `gate.py`, `cli.py` | A test for each level shows which gates asked | C12 |
| [x] C14 | **Deterministic risk floor**: `effective_risk = max(ecp.risk, rule_risk)`. Rule risk is high for: protected paths, dependency manifests, CI/config, auth/crypto paths, `feature` type, more files than `max_files`, a type not in `allowed_change_types` | `gate.py` | An ECP the LLM marks low that touches `requirements.txt` is high | C12 |
| [x] C15 | **Risk re-checked after the build** on the real diff: changed files, diff lines over budget, existing tests edited (C43) | `gate.py`, `engine.py` (before the result gate) | A 1-file plan whose diff touches 5 files becomes high risk at the result gate | C14 |
| [x] C16 | Elephant's `risk` becomes advisory: removed from its required output, kept as `risk_suggested` | `elephant.py`, `ecp.py` | The gate never reads the LLM value alone | C14 |
| [x] C17 | **The lock is checked from disk.** Before `executing`, before `decided` and before merging: reload the ECP from the file and compare `compute_hash()` with the `plan_hash` in the evidence `status→approved` entry | `lifecycle.py`, `engine.py`, `ecp.py` | Editing the ECP file during the build makes the run abort with "plan changed after approval" | C01 |
| [x] C18 | **Approvals bound to the plan.** `approval.json` = `{ecp_id, plan_hash, approve, approver, time}`; the gate rejects it if the id or hash doesn't match. The pending file includes the hash | `cli.py:_ask_factory`, `gate.py`, `dashboard/server.py` (`/api/approve`) | A stale approval for an older version is ignored | C17 |
| [x] C19 | **Evidence chain anchored in git**: the record commit message gets the trailer `Evidence-Head: <hash>` for each ECP; `evolve show` checks it | `decide.py:commit_records`, `cli.py:cmd_show` | Rewriting the evidence file shows as a mismatch with the trailer | none |
| [x] C20 | **Deterministic plan checks** in `ECP.problems()`: no `**`/`*` at the root of `allowed_paths`; matched file count ≤ `max_files`; each allowed glob matches an existing file **or** is listed in a new field `new_files`; `bug` needs at least one test path in scope | `ecp.py`, `elephant.py` (prompt adds `new_files`) | Each rule has a failing-ECP test | C12 |

## Phase 3: Goldfish (the main contribution)

| ID | Change | Files | Done when | Needs |
|---|---|---|---|---|
| [ ] C21 | **Context contract per lens**: `Lens.inputs` ⊂ {`ecp`, `rules`, `allowed_files`, `neighbourhood`, `diff`, `test_output`}. comprehension/readiness = `ecp`; scope_rules = `ecp,rules`; root_cause/impact/behavior_preserved = `ecp,allowed_files,neighbourhood`; after-change lenses add `diff,test_output`. Build one folder per lens | `goldfish/runner.py` | A test asserts the exact files each lens's prompt contains | none |
| [ ] C22 | **Dependency neighbourhood** builder, with no LLM: Python AST (or grep on other languages) for importers of the allowed files and callers of the symbols they define, capped | new `evolve/neighbours.py` | For the shop fixture, the neighbourhood of `pricing.py` includes `report.py` | none |
| [ ] C23 | **Truncation manifest**: every context folder gets `CONTEXT_MANIFEST.md` listing the files included and the files left out (and why). `_common.md` tells reviewers to read it | `goldfish/runner.py:81`, `lenses/_common.md` | With more than 25 matches, the manifest lists the extra files | C21 |
| [ ] C24 | **Finding ledger** across rounds: fingerprint = sha(lens + location + normalised claim); state open/fixed/recurring/disputed; saved to `.evolution/ledger/ECP-x.json` | new `evolve/goldfish/ledger.py`, `engine.py` | After round 2, findings from round 1 show as fixed or recurring | none |
| [ ] C25 | Elephant's `revise` gets the **whole ledger** and its earlier versions, not just the current stoppers | `elephant.py:revise`, `engine.py` | The prompt contains the history from earlier rounds | C24 |
| [ ] C26 | **Non-convergence stop**: the same fingerprint blocking in 2 rounds in a row ends the loop early with `rejected: not converging on <claim>` | `engine.py` | Scenario fixture with a recurring finding stops at round 2 | C24 |
| [ ] C27 | **Disputes + adjudicator**: Elephant may return `disputes:[{fingerprint, reason}]`; each one goes to a new zero-context lens `adjudicator` (finding + rebuttal + the cited files → `uphold`/`dismiss`). Dismissed findings stop blocking and are kept in the evidence | `elephant.py`, new `lenses/adjudicator.md`, `goldfish/runner.py`, `engine.py` | Scenario: a disputed INSUFFICIENT CONTEXT major is dismissed, the plan proceeds, and the evidence shows both sides | C24 |
| [ ] C28 | **Structured outputs**: `LLM.complete(..., schema=dict)` sends Azure `response_format: {type: json_schema}`; used for findings, ECP, scout, claim, adjudicator. `extract_json` stays as a fallback | `llm.py`, `goldfish/runner.py`, `elephant.py`, `discover.py`, `MockLLM` | The live test shows no parse retries; mock tests unchanged | none |
| [ ] C29 | **Rewrite the lens prompts**: each gets a checklist, a severity rubric with one example per level, and an "out of scope for this lens" list | `goldfish/lenses/*.md` | The eval in C30 shows precision does not drop | none |
| [ ] C30 | **Goldfish eval harness**: `tests/goldfish_eval/cases/*.json` (seeded defects: wrong root cause, missing test, scope creep, injected bug, clean control). `evolve eval goldfish` reports recall/precision per lens and model, saved to `.evolution/eval/` | new `evolve/eval.py`, `cli.py`, cases | Runs on mock (CI) and on Azure (on demand), and prints a table | C21, C28 |
| [ ] C31 | **Concurrency limit**: `cfg.llm_concurrency` (default 4), one semaphore shared by every LLM call | `goldfish/runner.py:148`, `llm.py`, `config.py` | Never more than N calls in flight (counted in a test) | none |
| [ ] C32 | (P2) **Blocker agreement**: optional `blocker_votes: 3`, so a blocker counts only if 2 of 3 runs or models agree | `goldfish/runner.py`, `config.py` | Mock test with 1 of 3 agreeing demotes it to major | C28 |

## Phase 4: Pi, guard and sandbox

| ID | Change | Files | Done when | Needs |
|---|---|---|---|---|
| [ ] C33 | **Default-deny guard**: allow only `read, grep, find, ls` (scoped, C36) and `write, edit` (scoped). Block every other tool and block if `ECP_ACTION_LOG` is unset | `guard/guard.ts:25-30` | `test-hook.mjs`: an unknown tool name is blocked | none |
| [ ] C34 | **Real-path check**: resolve symlinks before `checkPath`; block if the real path leaves the repo or enters `.git`/`.evolution`. Apply it to the diff check in `scope.py` too | `guard/guard.ts`, `guard-core.mjs`, `scope.py` | Fixture with a `src/x → ../outside` symlink: write is blocked | none |
| [ ] C35 | **Case-insensitive matching** on Windows and macOS (or always for `.git`/`.evolution`), in both implementations; new cases in `scope_cases.json` | `scope.py`, `guard-core.mjs`, `tests/scope_cases.json` | `.GIT/hooks/x` and `.Evolution/x` are blocked by both | none |
| [ ] C36 | **Scope reads** to the repo root (no absolute paths outside it, no `..`) | `guard/guard.ts` | `read ../../.env` is blocked and logged | C33 |
| [ ] C37 | **Move `workspaces/` out of the project folder**, to `~/.evolve/workspaces/` (configurable with `EVOLVE_HOME`) | `cli.py`, `prepare.py:28`, `dashboard/server.py`, docs | No cloned repo lives under the folder that holds `.env` | none |
| [ ] C38 | **One runner image** (extend `Dockerfile.pi` with Python, pip, git) and run `prepare`, baseline tests, discovery detectors, verification and fail-before inside it when `sandbox: podman` | `sandbox/Dockerfile.pi`, new `evolve/sandbox.py` (`run_in_sandbox(cmd)`), `verify.py`, `prepare.py`, `discover.py` | With Podman on, no target-repo code runs on the host (assert via a canary file) | C37 |
| [ ] C39 | **Podman hardening**: `--read-only`, `--tmpfs /tmp`, `--pids-limit 512`, `--memory 2g`, network limited to the gateway (C42) | `pi_runner.py:podman_command`, `evolve/sandbox.py` | `podman_command` test asserts the flags | C38 |
| [ ] C40 | **`run_tests` tool for Pi**: a Pi extension tool that calls the control plane's sandboxed test runner and returns the last 3,000 characters. Every call is logged as an action | `guard/` (new tool), `pi_runner.py` | A Pi action log shows `run_tests` calls; bash stays blocked | C38 |
| [ ] C41 | **Bounded build retries**: if verify fails on tests or a check (not on scope), feed the result back to Pi, up to `governance.max_build_attempts` (default 2), **under the same locked ECP**; every attempt is recorded | `engine.py`, `pi_runner.py` | Scenario: attempt 1 fails, attempt 2 passes, the change is accepted, the hash is unchanged | C01, C17 |
| [ ] C42 | **LLM gateway**: a small local proxy (stdlib HTTP) that holds the Azure key, takes calls from Pi and the control plane, enforces the token budget, and writes the call ledger (C59). `models.json` points at the gateway with a per-run random token, never the real key | new `evolve/gateway.py`, `pi_runner.py`, `azure.py` | `grep` of `.evolution/run/` during a run finds no Azure key | none |
| [ ] C43a | **Context folder for Pi**: a read-only `CONTEXT/` with the finding evidence and the baseline failure output, alongside the ECP | `pi_runner.py` | The Pi prompt references `CONTEXT/` | none |

## Phase 5: Verification

| ID | Change | Files | Done when | Needs |
|---|---|---|---|---|
| [ ] C43 | **Test-tampering check**: a diff that deletes or edits assertion lines in tests that existed at `base_commit` makes risk high; for `bug`/`refactor` it fails verification unless the ECP lists those tests in `allowed_paths` with a reason | `verify.py`, `gate.py` | A fixture that loosens an existing `assert` gets rolled back | C15 |
| [ ] C44 | **Compare against the baseline**: pass = no new failing tests compared with `prepare.json`, plus the targeted tests pass (instead of requiring the whole suite to be green). Store the failing test IDs at baseline | `prepare.py` (save IDs from junitxml), `verify.py` | Repo with an unrelated failing test: a fix for another bug is accepted | C04 |
| [ ] C45 | **Static regression gate**: bandit + ruff on the changed files at the base and after the change. New bandit alerts fail; new ruff issues become nit findings | `verify.py`, reuse `discover.d_bandit/d_ruff` with a file filter | Injecting `eval(` in a fixture change fails verification | none |
| [ ] C46 | **Diff coverage**: every added or changed line in a non-test file must be run by the tests (coverage json + diff hunks); threshold from governance (default 80%) | `verify.py` | A change adding an untested branch fails or warns as configured | C12 |
| [ ] C47 | **Flake control**: rerun a failing test command once; record `flaky` if the results differ | `verify.py` | A flaky fixture is marked flaky, not rolled back silently | none |

## Phase 6: Control plane, ECP, Elephant, discovery, model access

| ID | Change | Files | Done when | Needs |
|---|---|---|---|---|
| [ ] C48 | **Short-circuit**: skip the post-change Goldfish review if Pi errored, made no changes, or tests/scope failed | `engine.py` | A test-fail scenario makes 0 post-change LLM calls | none |
| [ ] C49 | Record the verify verdict on the `verified` transition (`note="passed"/"failed"`); either use `reviewed→proposed` for revisions or delete it | `engine.py:156`, `lifecycle.py` | The evidence shows the verdict; there are no unused transitions | none |
| [ ] C50 | **Resumable engine**: split `_run` into step functions; write `.evolution/run/checkpoint.json` after each step; `evolve resume ECP-x` continues. A high-risk approval exits and resumes instead of polling for an hour | `engine.py`, `cli.py` | Kill the process at the gate, approve, resume, and it completes | C01, C18 |
| [ ] C51 | **ECP schema additions**: `schema_version`, `origin {kind, ref}`, `new_files`, `risk_suggested`, `outcome {result, reasons, lessons, decided_at}` written at `decided` (outside the hashed plan part) | `ecp.py`, `engine.py`, `decide.py` | Old ECP files still load (migration default) | C16, C20 |
| [ ] C52 | Full SHA-256 for the plan and evidence hashes; UTC ISO timestamps with `Z` | `ecp.py`, `decide.py`, `discover.py` | Existing chains still verify (accept both lengths when reading) | none |
| [ ] C53 | File lock around `next_id` and `next_finding_number` | `ecp.py`, `discover.py` | Two concurrent drafts get different IDs | none |
| [ ] C54 | **Elephant context ranked by relevance**: the finding's file → its imports and importers (C22) → its tests → README/rules → fill to the budget | `elephant.py:repo_overview` | On a repo over the budget, the finding's file is always included | C22 |
| [ ] C55 | **Structured history**: `history.jsonl` adds `failed_checks`, `files`; Elephant gets only past ECPs that touched the same files (plus the last 5) | `decide.py:remember`, `elephant.py:history` | The draft prompt includes the related past ECP and not unrelated ones | none |
| [ ] C56 | Discovery runs the test suite **once** (one coverage+durations run supplies the failing tests, coverage and timings); reuse the junitxml from prepare when it is fresh | `discover.py` | Discovery makes exactly one test run (counted in a test) | C44 |
| [ ] C57 | **Finding fingerprints**: sha(file + rule + normalised quote). Re-discovery updates the existing F-id (`still_present`/`gone`) instead of adding a duplicate | `discover.py` | Running discovery twice gives the same set of IDs | none |
| [ ] C58 | **LLM call ledger**: every call adds `{role, deployment, prompt_sha, response_sha, tokens_in, tokens_out, latency_ms, finish_reason}` to the ECP evidence (or `discovery.jsonl`). Optional full text in `.evolution/llm/` | `llm.py`, `engine.py`, `discover.py` | `evolve show` prints tokens per role | C42 (or done in-process first) |
| [ ] C59 | Retry logic: honour `Retry-After`, add jitter, make the token cache thread-safe (lock) | `llm.py:_post`, `azure.py:token` | Unit test: a 429 with `Retry-After: 3` waits 3 seconds | none |
| [ ] C60 | Fixed `temperature` per role (and `seed` if the deployment supports it) in config | `config.py`, `llm.py` | The request payload contains them | none |
| [ ] C61 | (P2) **Reproduce step**: for a `bug` finding at evidence 1–2, `evolve reproduce F-x` runs a mini-ECP whose scope is tests only; a failing test raises it to evidence 3 | `discover.py`, `engine.py`, `cli.py` | Fixture finding moves from 2 to 3 | C41 |
| [ ] C62 | (P2) **Detector registry** + `semgrep` (multi-language) + ESLint for JS/TS, each skipped if missing | `discover.py` | A JS fixture produces semgrep signals | none |
| [ ] C63 | (P2) **Auto-pick at L3**: a plain rule (evidence ≥2, impact ≥ medium, fix small, type allowed) picks the next finding | `discover.py`, `engine.py` | At L3 the top eligible finding runs without a person | C13, C57 |

## Phase 7: Decide, dashboard, hygiene

| ID | Change | Files | Done when | Needs |
|---|---|---|---|---|
| [ ] C64 | **Merge modes** from governance: `merge` (now), `pr` (push the branch and open a PR with the ECP as the description, via `gh` or the API), `patch` (write `.evolution/patches/ECP-x.patch`) | `decide.py`, `governance.py` | Each mode tested (PR mode with a mocked `gh`) | C12 |
| [ ] C65 | **Records on an orphan branch** `evolve/records` (worktree) instead of commits on the base branch; option to keep the current behaviour | `decide.py:commit_records` | A rejected run adds 0 commits to main | none |
| [ ] C66 | Rollback also removes ignored files Pi created (paths from the action log only) | `decide.py:rollback` | A fixture where Pi writes to an ignored path ends clean | none |
| [ ] C67 | Venv folder keyed by `owner-name-hash`, not the folder name | `prepare.py:env_dir` | Two repos with the same name get different venvs | C37 |
| [ ] C68 | Approve panel: sends `ecp_id + plan_hash`, shows the diff from the previous plan version and the effective risk with its reasons | `index.html`, `dashboard/server.py` | The page test posts the hash; a mismatch is refused | C18 |
| [ ] C69 | (P2) Server-Sent Events `/api/stream` replacing the two 1-second polls | `dashboard/server.py`, `index.html:616-617` | The page updates without polling | none |
| [ ] C70 | (P2) **Metrics page**: outcomes, rounds per ECP, blocking findings per lens, blocked edits, tokens and time per run, eval results (C30) | `dashboard/server.py`, `index.html` | Rendered from `.evolution/` files only | C30, C58 |
| [ ] C71 | One typed event model (pydantic) used by `live.emit` and `Evidence.add` | new `evolve/events.py`, `live.py`, `ecp.py` | An unknown event kind is rejected in tests | none |
| [ ] C72 | Delete `docs/.env`; pin `bandit`, `ruff`, `coverage`, `pytest` versions in `pyproject.toml` | `docs/.env`, `pyproject.toml` | `pip install -e .` gives the same detector versions everywhere | none |
| [ ] C73 | Update the docs: README (new data locations, governance), HOW_IT_WORKS (context contracts, ledger, adjudicator, risk floor, autonomy levels), SETUP/OFFICE_LAPTOP (`~/.evolve`), DASHBOARD (token, approve panel) | `README.md`, `docs/*.md` | The docs match the code | whichever changes are done |

## Phase 8: Tests that cut across components

| ID | Change | Files | Done when | Needs |
|---|---|---|---|---|
| [ ] C74 | **Adversarial suite**: symlink escape, `.GIT/` case, CSRF text/plain POST, repo-supplied config, ImportError fail-before, wrong-shape Goldfish reply, exception mid-run, LLM-written shell check | new `tests/test_adversarial.py` | All pass | C01–C10, C33–C36 |
| [ ] C75 | **Scope parity fuzz**: generate random paths and globs, and require `scope.py` and `guard-core.mjs` to give the same verdict | `tests/test_guard_js.py` (or a new parity test) | 1,000 random cases agree | C34, C35 |

---

### Count

| Phase | Changes |
|---|---|
| 1 P0 fixes | 10 |
| 2 Governance | 10 |
| 3 Goldfish | 12 |
| 4 Pi / guard / sandbox | 11 |
| 5 Verification | 5 |
| 6 Engine, ECP, Elephant, discovery, LLM | 16 |
| 7 Decide, dashboard, hygiene | 10 |
| 8 Cross-cutting tests | 2 |
| **Total** | **76** |
