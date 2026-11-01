# Evolve: architecture review and improvement plan

Reviewed 28 Oct 2026 against commit `16e2ec1`. All 20 Python modules (about 3,000 lines), the guard, the sandbox,
the 13 lens prompts, the dashboard and the tests were read. The test suite passes on this machine: **115 passed, 7 skipped**
(the skipped ones are the live Azure/Pi tests).

Every item below makes the existing design stronger. None of them replaces a part of it.

---

## 0. The core (unchanged)

| Invariant | Where it lives today | Status |
|---|---|---|
| One ECP per change, locked once approved, followed by an append-only record | `ecp.py`, `lifecycle.py` | Keep. Harden the lock (see 3.2) |
| Elephant plans and never writes code | `elephant.py`, `discover.py` (scout) | Keep |
| Goldfish: reviewers with zero context, one lens each, fail closed | `goldfish/` | Keep. This is the main contribution, so most P1 work goes here (3.4) |
| Pi builds only inside the ECP's scope, enforced and not just requested | `pi_runner.py`, `guard/` | Keep. Close the gaps (3.5) |
| The control plane contains no AI; plain programs decide | `engine.py`, `gate.py`, `verify.py`, `decide.py` | Keep |
| A bug fix needs proof: the new test fails on the old code and passes on the new | `verify.py` | Keep. Fix the false "proved" (3.6) |
| A human approves high-risk work | `gate.py` | Keep. Make risk deterministic (3.3) |

---

## 1. Architecture map

```
                         ┌──────────────────── cross-cutting ─────────────────────┐
                         │ llm.py/azure.py (model access)   live.py (event feed)    │
                         │ ecp.Evidence (hash chain)        config.py (.evolution/) │
                         │ cli.py  ·  dashboard/server.py + index.html               │
                         └──────────────────────────────────────────────────────────┘
 INTAKE            DISCOVER            PLAN           REVIEW            BUILD          VERIFY            DECIDE          MEMORY
 repo.py      ──►  discover.py    ──►  elephant.py ─► goldfish/   ──►   pi_runner.py ► verify.py    ──►  gate.py     ──►  decide.py
 prepare.py        detectors(no AI)    draft/revise   runner+lenses     guard/*.mjs/ts  tests, scope,    risk gate        merge/rollback
                   scout(LLM)          ▲      │       (before)          Podman          fail-before      human ask        history.jsonl
                   claim-check(LLM)    └─revise┘                                        + Goldfish(after)                 commit records
                                       lifecycle.py: proposed → reviewed → approved(LOCK) → executing → verified → decided
```

### Trust boundaries (the most important architectural view)

| Input | Trusted today? | Should be |
|---|---|---|
| Target repo code, tests, `setup.py` | Runs on the host (prepare, discovery, verify). Only Pi is sandboxed | **Untrusted**: everything that executes it goes in the sandbox |
| Target repo's own `.evolution/config.yaml`, `rules.md` | Used as-is if the repo ships them (`server.py:215`) | **Untrusted**: the repo being governed must not be able to set its own governance |
| LLM output: ECP `verification[].command`, `risk` | Commands run with `shell=True` on the host; risk is self-declared | **Untrusted**: commands go through an allowlist or the sandbox; risk gets a deterministic floor |
| LLM output: Goldfish JSON | Fails closed on bad JSON, but fails **open** on one shape (see 3.4) | Fail closed on everything |
| Dashboard HTTP requests | Any web page can POST to it (no Origin or token check) | Same-origin only |
| Pi actions | Writes are guarded; reads and unknown tools are allowed by default | Default deny |

---

## 2. Priority summary

**P0: bugs and safety holes. Fix these before the mentor demo.**

| # | Item | File |
|---|---|---|
| P0-1 | A crash mid-run leaves the repo on `evolve/ECP-x` with half-applied edits and the ECP stuck in `executing` | `engine.py` |
| P0-2 | A Goldfish reviewer that throws crashes the whole run. A reply shaped `{"result":[...]}` is read as "no findings" (fails open) | `goldfish/runner.py:116,144` |
| P0-3 | The fail-before proof can report "proved" for the wrong reason (ImportError, or a test that was already failing) | `verify.py:83-111` |
| P0-4 | Dashboard CSRF: any website can change `AZURE_OPENAI_BASE`, so the next call sends your key to that site. It can also approve gates and start runs | `dashboard/server.py:269` |
| P0-5 | The governed repo can supply its own `config.yaml` (test command, sandbox off) and `rules.md` | `dashboard/server.py:215`, `cli.py` |
| P0-6 | Verification commands written by the LLM run with `shell=True` on the host | `verify.py:47` |
| P0-7 | `--yes` is recorded as "human approved" in the evidence | `gate.py:37`, `cli.py` |
| P0-8 | `read_text()` without an encoding crashes on Windows when a title contains non-ASCII text | `elephant.py:65`, `cli.py:223`, `server.py:63`, `cli.py:39`, `discover.py:163` |
| P0-9 | Prepare installs any package named in a "No module named X" error, so an untrusted repo can make you install an arbitrary package | `prepare.py:157-162` |

**P1: strengthen the core.** Governance profile, deterministic risk, a real lock, sandbox for all execution, default-deny guard,
a Pi feedback loop, Goldfish context contracts and convergence, a Goldfish eval harness, verification that compares against the baseline, and an LLM call ledger.

**P2: polish and scale.** ECP schema versioning, records off the main branch, PR mode, deduplicated discovery, a resumable engine, metrics.

---

## 3. Component by component

### 3.1 Control plane: `engine.py`, `lifecycle.py`

What it does well: a linear, readable, AI-free sequence; every step emits a live event and writes an evidence entry.

| Pri | Improvement | Why / how |
|---|---|---|
| **P0** | Wrap everything after `start_branch` (`engine.py:135`) in `try/except/finally`. Any exception means `rollback`, outcome `aborted`, an evidence entry with the traceback, and `_finish` | Today an Azure 500 during the post-review, or a git failure in verify, leaves the repo dirty on the evolve branch. The next run then refuses because "the repo has uncommitted changes" |
| P1 | Short-circuit: skip the post-change Goldfish review when Pi errored, made no changes, or the tests/scope failed | That review is LLM spend on a change that is already certain to be rolled back |
| P1 | Turn the engine into named steps (`plan`, `review`, `approve`, `build`, `verify`, `post_review`, `decide`), each `(state) -> state`, and write a checkpoint after each one | Lets a crashed or interrupted run resume. A high-risk approval can wait for hours without holding a process open (today `cli.py` polls for up to 1 hour) |
| P2 | `verified` is set even when verification failed (`engine.py:156`). Either record the verdict on the transition (`note="failed"`) or add a status for "verification ran" | Makes the lifecycle honest when read on its own |
| P2 | The `reviewed → proposed` transition exists but is never used. Revisions bypass the lifecycle. Either route revisions through it or remove it | Removes dead governance rules |

### 3.2 ECP model and evidence: `ecp.py`

What it does well: a YAML+Markdown file humans can read, with a deterministic completeness check, a lock hash and a hash-chained evidence log.

| Pri | Improvement | Why / how |
|---|---|---|
| P1 | **Make the lock mean something.** `lock_intact()` recomputes the hash from the in-memory object, and every transition re-saves it, so an edit to the file on disk is overwritten, never detected. Before `executing`, before `decided`, and before merging, reload the ECP from disk and compare it to the `plan_hash` recorded in the evidence at approval | The guarantee should come from the record, not from the object checking itself |
| P1 | Anchor the chain: write the evidence head hash into the record commit message (`decide.commit_records`) as a trailer, `Evidence-Head: <hash>` | Today someone can rewrite the whole chain consistently. Git history makes that visible |
| P1 | **Bind approvals to a plan.** `approval.json` should hold `{ecp_id, plan_hash, approver, time}`, and the gate should reject an approval whose hash doesn't match | An approval should be for this exact plan, not for "whatever is pending" |
| P1 | Deterministic checks in `problems()`: reject root-level `**` or `*` in `allowed_paths`; cap the number of matched files; every allowed glob must match an existing file unless it is listed under a new `new_files` field; bug fixes need at least one test path in scope | Catches over-broad scope without asking an LLM |
| P2 | Add `schema_version`, `origin` (`{kind: request\|finding\|failing_tests, ref: F-001}`, which today lives only in the evidence) and an `outcome` block (`result`, `reasons`, `lessons`) written at `decided` | Your ECP definition covers before, during **and after** the change, and retaining knowledge. Today the "after" half lives only in history.jsonl |
| P2 | Use full SHA-256 instead of `[:16]`, and UTC ISO timestamps with a timezone in the evidence | Small change, avoids questions in a review |

### 3.3 Governance and the risk gate: `gate.py`, `config.py`

This is where the long-term goal ("governed change at a defined governance level") becomes concrete.

| Pri | Improvement | Why / how |
|---|---|---|
| P1 | **An evolution / governance profile** (`governance.yaml`, kept *outside* the target repo, per connected repo): autonomy level, change types allowed, max files and diff lines, required lenses per type, which gates need a human, model/token budget, merge mode | Turns "controlled autonomy" from an idea into a config file a mentor can read. Suggested levels: **L0** discover only · **L1** plan and review, human approves everything · **L2** low risk runs on its own · **L3** low risk runs on its own and also picks its own findings |
| P1 | **A deterministic risk floor**: `effective_risk = max(llm_risk, rule_risk)`, where `rule_risk` is high if the change touches protected paths, dependency manifests (`requirements*.txt`, `pyproject.toml`, `package.json`), CI/config, auth/crypto paths, changes or deletes existing test assertions, crosses N files or M diff lines, or has `change_type=feature` | Today Elephant (an LLM) declares `risk: low`, and only protected paths can raise it. The gate's own docstring says "never decided by an AI" |
| P1 | Re-evaluate risk **after the build**, on the real diff (lines, files, test edits), not only on the plan | The plan can say "1 file" while the diff touches 4. Scope catches paths but not size |
| **P0** | Record the approval mode truthfully: `auto (low risk)`, `auto-yes (CLI flag)`, `human: <who>` | `--yes` currently writes "human approved", which is false in an audit trail |

### 3.4 Goldfish: `goldfish/runner.py`, `lenses/*.md`

What it does well: a context folder per run, selection by plain rules (`roster()`), parallel lenses, an "INSUFFICIENT CONTEXT" rule, failing closed on JSON it can't parse.

| Pri | Improvement | Why / how |
|---|---|---|
| **P0** | In `one()`, catch the exception and return a `major` finding ("reviewer unavailable: …") instead of `raise` (`:144`) | Matches the fail-closed principle. One rate-limited lens no longer kills the run |
| **P0** | In `_parse`, accept a list, or a dict **that has** a `findings` key. Anything else is a fail-closed finding (`:116`) | `{"issues":[...]}` is currently read as "all clear" |
| P1 | **A context contract per lens.** Each lens declares its inputs: `comprehension` and `readiness` get the **ECP only** (a real "stranger" test); `scope_rules` gets ECP + RULES; `root_cause` and `impact` get ECP + allowed files + the **dependency neighbourhood** (importers and callers of the changed symbols, found by grep/AST, not by an LLM); after-change lenses add the DIFF | Today every lens sees the same folder. `root_cause` can only see files the plan already chose, so it can't spot a cause in a file the plan left out. This sharpens your innovation without changing it |
| P1 | **Disclose truncation.** When `MAX_FILES`/`MAX_FILE_BYTES` drop files (silently, `:81`), write a `CONTEXT_MANIFEST.md` listing what was left out | The reviewer can then say INSUFFICIENT CONTEXT instead of judging from partial input |
| P1 | **Convergence.** Keep a finding ledger across rounds (fingerprint = lens + location + normalised claim). Pass the full ledger to Elephant (it is the long-memory role). Detect findings that come back. Let Elephant mark one `disputed` with a reason, and send it to a zero-context **adjudicator** Goldfish (finding + rebuttal + files → uphold/dismiss) | Today Elephant sees only the current round's stoppers, so plans can oscillate. An unresolvable INSUFFICIENT CONTEXT major blocks every round until the plan is rejected |
| P1 | **Structured outputs**: Azure `response_format: json_schema` for findings, ECPs, scout output and claim checks | Removes most of `extract_json`'s fragility and the retry calls |
| P1 | **A Goldfish eval harness**: a set of seeded-defect ECPs and diffs (wrong root cause, missing test, scope creep, injected bug), scored by recall/precision per lens, per model, per prompt version. You already have the `scenarios/` fixtures to start from | This is the evidence that Goldfish works. It is the research claim, and the mentor will ask for numbers |
| P1 | Richer lens prompts: each one is two lines today. Add a checklist, a severity rubric with one example per severity, and "what is NOT in this lens" | Fewer majors that drift outside the lens's scope, and severity that stays consistent |
| P2 | Model diversity per lens (already supported by `model_key`). Optionally require 2-of-3 agreement for a `blocker` | Reduces correlated blind spots, which DISCOVERY.md already admits |
| P2 | Limit parallelism (`max_workers = min(len, cfg.goldfish_concurrency)`) and share one semaphore with the LLM client | 8 lenses at once on one deployment invite 429s |

### 3.5 Pi, the guard and the sandbox: `pi_runner.py`, `guard/`, `sandbox/`

What it does well: a two-layer scope (guard during the build, diff check after), one test-cases file shared by Python and JS, bash off, `--cap-drop=ALL`, the provider file deleted after the run.

| Pri | Improvement | Why / how |
|---|---|---|
| P1 | **Default-deny guard.** Allow only an explicit list (`read, grep, find, ls` plus `write/edit` under scope). Block any other tool name, and block if `ECP_ACTION_LOG` is missing (`guard.ts:25-30` returns `undefined`, which allows, for unknown tools) | The `--tools` flag already limits this, but the guard should not rely on another layer |
| P1 | **Real-path and case checks.** Resolve symlinks (`realpath`) before `checkPath`, and refuse if the real path leaves the repo or lands in `.git`/`.evolution`. On Windows and macOS, compare case-insensitively | A cloned (untrusted) repo can contain a symlink `src/x → ../../.env`. `.GIT/hooks` matches neither `.git/**` nor an allow rule only by luck |
| P1 | **Scope reads too**, to the repo root, and move `workspaces/` **out of** the evolve project folder (`prepare.py:28`, `cli.py`) | Repos are cloned under the evolve folder today, so a local Pi can `read ../../.env`, the file that holds your Azure key |
| P1 | **A feedback loop without a shell**: give Pi a custom `run_tests` tool, implemented by the control plane and sandboxed, which returns the trimmed output. Alternatively, retry the build up to N times **inside the same locked ECP**, feeding the verify output back | Pi currently writes a fail-before test it can never run. Most rollbacks will be "tests failed" on the first try. The plan stays locked; only the implementation iterates |
| P1 | Keep the key out of the agent: a small local **LLM gateway** (or a short-lived Entra token only) that Pi calls, instead of `models.json` holding `apiKey` inside the repo's `.evolution/run/` | The same gateway also gives you the call ledger (3.9) and egress control |
| P2 | Podman: add `--network` restricted to the gateway, `--read-only` root FS plus tmpfs, `--pids-limit`, `--memory` | Defence in depth for untrusted repos |
| P2 | Give Pi the finding evidence and the baseline failure along with the ECP (read-only `CONTEXT/` folder) | It currently gets only the ECP markdown. For bug fixes the reproduction matters |

### 3.6 Verification: `verify.py`

What it does well: deterministic checks only; scope re-checked on the final diff; a real fail-before/pass-after proof that restores the working tree in `finally`.

| Pri | Improvement | Why / how |
|---|---|---|
| **P0** | **Make the fail-before proof precise.** Run **only the new or changed test files** on the old code (pytest node IDs). Require that they *fail on assertions*, not on collection or ImportError (parse the pytest outcome or JUnit XML). Also require that the old code without the new tests matches the baseline | Today, if the fix adds `utils/new.py` and the test imports it, restoring the old code deletes the file, so ImportError and the run counts as "proved". Any test that was already failing also counts as "proved" |
| **P0** | Run ECP `verification[].command` only if it matches an allowlist (`pytest …`, `python -m pytest …`, `npm test …`), or inside the sandbox | These commands are written by the LLM, and low-risk plans run them with no human in the loop |
| P1 | **Compare against the baseline, not "everything is green"**: pass = no new failures compared with `prepare.json`, plus the targeted tests pass | Discovery turns failing tests into findings. Fixing one of them on a repo with other failures always rolls back today |
| P1 | **Test-tampering check**: a diff that deletes or loosens assertions in *existing* tests sets risk to high, or fails for `bug`/`refactor` | The cheapest way for an agent to "fix" a bug is to edit the test |
| P1 | **Reuse discovery as a regression gate**: run bandit/ruff on the changed files before and after. New alerts are a failure (security) or a finding (quality) | Same detectors, so the before and after numbers are directly comparable |
| P1 | **Diff coverage**: new or changed lines must be run by the tests (coverage is already a dependency) | "Tests pass" says nothing if the new branch never runs |
| P2 | Flake control: rerun failures once, and mark tests that are flaky at the baseline | Avoids rollbacks caused by noise |

### 3.7 Elephant: `elephant.py` (and the scout in `discover.py`)

| Pri | Improvement | Why / how |
|---|---|---|
| P1 | **Context ranked by relevance**, not alphabetical (`repo_overview`, `:45`). Order: the finding's file, its imports and importers, its tests, README/rules, then fill up to the budget | On a repo larger than 150 KB, the relevant file can simply never reach Elephant |
| P1 | Structured history: `history.jsonl` lines carry the *failed check* and the files involved. Elephant gets the past ECPs that **touched the same files**, not a list of everything | "Learns from past changes" becomes retrieval instead of a dump |
| P1 | Elephant proposes risk but does not decide it (see 3.3). Remove `risk` from its required output, or keep it as advice only | Keeps the AI-free gate honest |
| P2 | During revision, Elephant sees the whole finding ledger and its own earlier versions (see 3.4) | It is the long-memory role, so give it the memory |

### 3.8 Discovery: `discover.py`

What it does well: signals from plain programs, a quote check by program, a zero-context claim check, evidence levels computed by program, drops logged with reasons.

| Pri | Improvement | Why / how |
|---|---|---|
| P1 | Run the test suite **once** per discovery. `d_tests` and `d_coverage_and_speed` each run it, and prepare already did, so it runs 3 times | Large repos take 3× as long, and flaky results can disagree |
| P1 | **Finding fingerprints** (file + rule + normalised quote). Re-running discovery updates the existing finding (`still present` / `gone`) instead of creating a new F-id | Without this, discovery fills up with duplicates after a few runs |
| P2 | A **reproduce** step: for a `bug` finding at evidence 1–2, offer a "write a failing test" mini-evolution (scope = tests only) that raises it to evidence 3 before the real fix is planned | Turns AI opinion into reproduced evidence, which fits your evidence model |
| P2 | A pluggable detector registry plus **semgrep** (multi-language security) and ESLint for JS/TS | Moves beyond "Python only" without rewriting anything |
| P2 | At L3 autonomy, a deterministic **auto-pick policy** (evidence ≥ 2, impact ≥ medium, fix size small, type allowed by the profile) | The next step after "a human picks the finding" |

### 3.9 Model access: `llm.py`, `azure.py`

| Pri | Improvement | Why / how |
|---|---|---|
| P1 | **An LLM call ledger**: each call adds `{role, deployment, prompt_sha, response_sha, tokens_in/out, latency, finish_reason}` to the ECP evidence (with full prompts optionally under `.evolution/llm/`) | Audit and reproducibility ("which model said this blocker?"), plus cost per ECP. A governed system should be able to answer both |
| P1 | Respect `Retry-After`, add jitter, set a global concurrency limit, and make the token cache thread-safe | Goldfish fans out in parallel |
| P2 | Pin `temperature` (and `seed` where supported) per role | Repeatable reviews for the eval harness |

### 3.10 Intake: `repo.py`, `prepare.py`

What it does well: tolerant link parsing, a token sent as a header and masked in logs, a shallow clone, a private venv, installing one package at a time when a requirements file fails.

| Pri | Improvement | Why / how |
|---|---|---|
| **P0** | Remove the "No module named X → pip install X" loop, or restrict it to packages declared in the repo's requirements/pyproject | Supply-chain risk from untrusted output |
| **P0** | On connect, ignore or quarantine a repo-supplied `.evolution/` (or show it and ask). Governance comes from evolve's own store | The repo being governed must not be able to define its own rules or test command |
| P1 | Run `prepare` (pip install `-e .` runs `setup.py`), the baseline tests and the discovery tools in the **same sandbox image** as Pi (one "runner" container image) | Today Podman protects only the one component that is already guarded |
| P2 | Key the venv by `owner-name-hash`, not by folder name | Avoids collisions |

### 3.11 Decide and records: `decide.py`

| Pri | Improvement | Why / how |
|---|---|---|
| P2 | **Merge mode** from the profile: `merge` (today), `pr` (push a branch and open a PR, so the PR is the human gate), `patch` (export only) | Real teams won't accept local merges into main. This is also a governance level |
| P2 | Write records to an orphan branch `evolve/records` (or git notes) instead of committing them onto the base branch | Rejected runs currently add commits to the user's main branch |
| P2 | `rollback` misses files Pi created in git-ignored paths. Use `git clean -fdx` limited to the paths Pi touched (from the action log) | Leaves a cleaner tree |

### 3.12 Dashboard: `dashboard/server.py`, `index.html`

| Pri | Improvement | Why / how |
|---|---|---|
| **P0** | CSRF/rebinding protection: reject a POST unless `Content-Type: application/json` **and** `Origin`/`Host` is `127.0.0.1:<port>`/`localhost:<port>`; add a random per-launch token that the page sends in a header | A `text/plain` form POST from any website is valid JSON to `json.loads` today, and can change `AZURE_OPENAI_BASE` to its own URL (the next call sends `api-key` there) or `/api/approve` |
| P1 | The approve button sends `ecp_id + plan_hash` (see 3.2) and shows the plan diff since the last version next to the button | The human approves what they actually saw |
| P2 | Server-Sent Events instead of two 1-second polls; a **metrics page** (outcomes, rounds per ECP, blocking findings per lens, blocked edits, tokens per run) | The metrics page is also your research results page |

### 3.13 CLI, config and repo hygiene

| Pri | Improvement |
|---|---|
| **P0** | Add `encoding="utf-8"` to every `read_text()` / `open()` (5 places listed in P0-8). You run this on Windows |
| P2 | Delete `docs/.env` (it is git-ignored, but a secrets file doesn't belong under docs). Move `workspaces/` out of the project folder |
| P2 | Pin versions of bandit, ruff, coverage and pytest so detector output stays stable between runs |
| P2 | One typed event schema (pydantic) shared by `live.emit` and `Evidence.add` |

### 3.14 Tests

| Pri | Improvement |
|---|---|
| P1 | Adversarial tests: symlink escape, case-variant `.GIT/`, CSRF text/plain POST, the fail-before ImportError case, a Goldfish reply with the wrong JSON shape, an exception mid-run leaving a clean tree |
| P1 | Fuzz the parity between `scope.py` and `guard-core.mjs` on random paths and globs (it already shares `scope_cases.json`, so extend that to generated cases) |
| P1 | The Goldfish eval harness (3.4) as its own suite, run on demand against the live model |

---

## 4. Suggested order

1. **Before the demo (about 1 day):** P0-1 to P0-9. Each is small and local to one file. Add one test per item.
2. **Week 1, governance:** governance profile + autonomy levels, deterministic risk floor (before and after the build), lock checked from disk/evidence, approvals bound to the plan hash.
3. **Week 2, Goldfish (your contribution):** context contracts per lens, truncation manifest, finding ledger + adjudicator, structured outputs, eval harness with first precision/recall numbers.
4. **Week 3, execution safety and capability:** one sandbox for all execution, default-deny + realpath guard, LLM gateway + call ledger, Pi `run_tests` tool / bounded retries, verification against the baseline + test-tampering check + diff coverage.
5. **Afterwards:** PR merge mode, records branch, discovery fingerprints + reproduce step, semgrep, resumable engine, metrics page.
