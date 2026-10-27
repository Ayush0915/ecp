# How it works (in simple words)

## The one idea

AI can change code faster than people can check it. So instead of trusting the AI,
we put **a written plan between the request and the code**, make independent reviewers
attack that plan, and only then let a builder act, inside strict limits.
That plan is the **ECP**.

## The ECP: one file per change

Lives at `.evolution/ecps/ECP-0001.md`. Top part is the **plan**, bottom part is reasoning.

| Field | Plain meaning |
|---|---|
| `intent` | What must be true after the change, and why |
| `change_type` | bug, feature, enhancement or refactor (decides which reviewers run) |
| `allowed_paths` | The ONLY files Pi may edit |
| `forbidden_paths` | Files that must never be touched |
| `constraints` | Rules to obey (from `rules.md` and common sense) |
| `success_criteria` | Statements that must be true when done |
| `verification` | How we prove it (tests or commands) |
| `abort_conditions` | When to stop and roll back |
| `rollback` | How to undo |
| `risk` | low = automatic, high = a human must say yes |

Two halves:

- **Plan half is locked** the moment the plan is approved. A fingerprint (hash) is stored.
  If anyone changes the plan afterwards, the fingerprint no longer matches and the run refuses to continue.
- **Record half is append-only**: `.evolution/evidence/ECP-0001.jsonl`. Every event is one line,
  and each line contains the fingerprint of the line before it. Editing history breaks the chain and is detected.

Life of an ECP: `proposed -> reviewed -> approved -> executing -> verified -> decided`.
Steps cannot be skipped (`evolve/lifecycle.py`).

## The five steps

**1. Plan: Elephant** (`evolve/elephant.py`)
Reads your request, the repo files, `rules.md` and the history of past ECPs, then writes the ECP.
When reviewers object, Elephant revises the same ECP (up to 3 times).

**2. Review: Goldfish** (`evolve/goldfish/`)
Several reviewers run at the same time. Each is given a small temporary folder containing only:
the ECP, `rules.md`, and the source files the ECP names. Nothing else: not your request,
not Elephant's thinking, not the other reviewers' answers. Each looks through one lens:

| Before building | Question it asks |
|---|---|
| Comprehension | Can a stranger tell what will change? |
| Intent fit | Does this plan really deliver the intent? |
| Verification | If all checks pass, do we truly know it works? |
| Scope & rules | Is scope minimal, and does it obey the rules? |
| Readiness | Could someone start work from this alone? |
| Root cause *(bugs)* | Does it fix the real cause, not just the symptom? |
| Impact *(features, enhancements)* | What else could this break? |
| Behavior preserved *(refactors)* | Does behavior stay identical? |

| After building | Question it asks |
|---|---|
| Conformance | Did it do what the ECP said, and only that? |
| Correctness | Is the code right? (runs on a *different* AI model than the builder) |
| Security, Performance *(features/enhancements)* | New risks? |

Findings are ranked **blocker / major / nit**. Blockers and majors send the plan back to Elephant
(or roll the change back after building). Nits are recorded only.
If a reviewer's answer cannot be read, it counts as a problem, never as "all clear".
Which reviewers run is decided by plain rules (`roster()`), not by an AI.

**3. Build: Pi** (`evolve/pi_runner.py`, `guard/`)
Pi works on its own git branch `evolve/ECP-0001`. A **guard** checks every write:
outside `allowed_paths`, in `forbidden_paths`, or in `.git/` / `.evolution/` means **blocked**,
and the attempt is logged. Pi also gets no shell by default (the control plane runs tests, Pi does not).

**4. Verify** (`evolve/verify.py`)
Ordinary programs, no AI: run the test command, run the ECP's checks, and compare the final diff
with the allowed paths. This catches anything the guard might have missed.
For **bug fixes** there is one more rule, the **fail-before / pass-after proof**: the system puts the OLD code back
(keeping the new tests), runs the tests, and they must FAIL. Then it restores the fix, and the tests must PASS.
A bug fix with no new test, or with a test that already passes on the old code, is rolled back as "no proof".

**5. Decide** (`evolve/gate.py`, `evolve/decide.py`)
- Everything green and no blocking findings: **accept** (merge the branch).
- Anything wrong: **roll back** (branch deleted, files restored).
- Risk gate: low risk is automatic; high risk asks a human. A plan that touches a repo's
  `protected_paths` is forced to high risk.
- The outcome is written to `.evolution/history.jsonl`, which Elephant reads next time. That is how the system learns.

## The control plane

`evolve/engine.py` runs the steps in order. It contains **no AI**. It never writes the plan,
never edits code, never judges quality. It only sequences, enforces rules and keeps records.
This is deliberate: the part that enforces safety should be boring and testable.

## Map of the code

| File | What it is |
|---|---|
| `evolve/ecp.py` | ECP model, completeness check, lock, evidence chain |
| `evolve/lifecycle.py` | Allowed state changes |
| `evolve/scope.py` | Path rules (allowed / forbidden / protected) |
| `evolve/elephant.py` | Planner |
| `evolve/goldfish/` | Reviewers: `runner.py` + one prompt per lens in `lenses/` |
| `evolve/pi_runner.py` | Runs Pi (real) or the scripted stand-in (demo) |
| `guard/guard.ts` | The Pi extension that blocks out-of-scope edits |
| `evolve/verify.py` | Tests and diff scope check |
| `evolve/gate.py` | Risk decision |
| `evolve/decide.py` | Merge, roll back, remember |
| `evolve/engine.py` | Runs everything in order |
| `evolve/llm.py` | AI connections (Anthropic, OpenAI-compatible, Mock) |
| `evolve/cli.py` | The `evolve` command |


## Added for the live demo

| Part | File | What it does |
|---|---|---|
| Azure connection | `evolve/llm.py` | Calls your GPT deployment; retries rate limits; readable errors |
| Pi in Podman | `evolve/pi_runner.py`, `sandbox/Dockerfile.pi` | Pi runs in a container that sees only the repo |
| Plan versions | `evolve/ecp.py` | Every revision of the plan is kept in `.evolution/versions/` |
| Dashboard | `evolve/dashboard/` | Live view and replay built from the evidence log; approve button; compare |
| Comparison | `evolve/compare.py` | Same request with a plain agent and with governance |
| Triggers | `evolve/cli.py` | Failing tests or an issue file can open an ECP |
| Doctor | `evolve/cli.py` | Checks this machine before a demo |
| "Insufficient context" | `goldfish/lenses/_common.md` | A reviewer that lacks information says so instead of guessing |

Honest limits: Goldfish reviewers are AI opinions. With one model family their blind spots overlap with the builder's.
The hard guarantees come from the lock, the guard, the scope check, the tests, the fail-before proof and rollback.
