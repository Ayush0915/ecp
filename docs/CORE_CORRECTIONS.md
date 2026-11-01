# Core corrections

This is not a feature list. Each item is a place where the design itself, as written in HOW_IT_WORKS and the code,
**contradicts its own principles** or rests on an assumption that does not hold. The core stays as it is:

> A written plan (ECP) sits between the request and the code. Elephant plans and never codes. Goldfish reviewers,
> with no memory of how the plan was made, attack it. Pi builds only what was approved, inside enforced limits.
> A control plane with no AI sequences everything, and plain programs decide.

The test applied to every item: *does it make the existing idea true, or does it add a new idea?* Only the first kind is here.
Everything else from CHANGES.md is parked (see the end).

---

## The principles the design claims

| # | Principle | Where it is claimed |
|---|---|---|
| P1 | The control plane contains no AI; plain rules decide | engine.py docstring, gate.py, HOW_IT_WORKS |
| P2 | Goldfish reviewers are independent of the plan's author | goldfish/runner.py, HOW_IT_WORKS |
| P3 | The approved plan is locked; any later change is detected | ecp.py, lifecycle.py |
| P4 | Proof comes from plain tools: a bug fix must show its test failing before and passing after | verify.py, README |
| P5 | Scope is enforced, not requested | guard/, scope.py |
| P6 | Fail closed: anything unreadable or broken counts as a problem, never as "all clear" | _common.md, runner.py |
| P7 | The ECP justifies, executes, verifies, evaluates and retains knowledge, before, during and after the change | your ECP definition |
| P8 | Elephant is the long-memory role; the system learns from past changes | elephant.py, HOW_IT_WORKS |

---

## A. Already corrected (Phases 1 and 2)

| # | What the design assumed | What was actually true | Correction | Principle |
|---|---|---|---|---|
| A1 | The repository's `.evolution/config.yaml` and `rules.md` are the repo's governance | The repository being governed could write its own rules and test command, so the governed thing defined its own governance | Governance lives outside the repo; files the repo ships are ignored | P1 |
| A2 | Hashing the plan locks it | The check compared the in-memory object with itself, and every save overwrote the file, so an edit on disk was never detected | The truth is the hash **recorded in the evidence at approval**; memory and disk are both checked against it, and approval names that hash | P3 |
| A3 | The risk gate is decided by plain rules | Elephant wrote `risk: low`, so the agent being judged chose how closely it is judged | Risk comes from rules on the plan and on the real diff; Elephant's opinion can raise it, never lower it | P1 |
| A4 | "The new tests fail on the old code" proves the bug | An ImportError, or any test that was already failing, also "failed", so the proof could be satisfied without showing the bug | Proved only if the same test fails (an assertion, not an import error) on the old code and passes on the new code | P4 |
| A5 | AI-written checks are just part of the plan | ECP `verification` commands, written by the LLM, ran through a shell on your machine, so the AI chose what got executed | Checks must be a known test runner and run without a shell | P1 |
| A6 | Reviewers fail closed | A reviewer that crashed stopped the whole run; one that replied `{"issues": [...]}` counted as "no findings" | Both now become blocking findings | P6 |
| A7 | A failed run is rolled back | A crash after the build started left the repo on the evolve branch with half-made edits | Any failure after the plan exists ends in rollback, a recorded `aborted` outcome, and a closed ECP | P6 |

---

## B. Still wrong in the design

### B1. The agent being judged still picks the label that sets its own scrutiny  (P1)
Elephant writes `change_type`, and that one word decides which Goldfish lenses run (`roster()`) and whether the
fail-before proof is required (`require_fail_before`). Labelling a bug fix as an `enhancement` removes the root-cause
lens and the proof. A finding's type is passed to Elephant only as a "suggestion" (`discover.py:430`).
**Correction:** the type is fixed **before** Elephant plans, by whoever started the change: the finding's type, the
request, or a person. Elephant may propose a different type only as a disagreement that the gate sees (it raises risk).
Never as a silent relabel.

### B2. What counts as proof comes from the plan, not from the change type  (P1, P4)
Only `bug` has a deterministic proof. For every other type, "proof" is whatever Elephant wrote in `verification`,
plus "the suite is green". A refactor can rewrite the tests that were supposed to show behaviour is unchanged.
**Correction:** each change type has a fixed proof obligation that plain programs check. The ECP's own `verification`
can add to it, never replace it:

| Type | Proof obligation (no AI) |
|---|---|
| bug | a new or changed test fails on the old code (assertion) and passes on the new code |
| refactor | existing tests are **not modified** and pass before and after |
| enhancement / feature | at least one new or changed test, and it passes; existing tests not weakened |

### B3. Goldfish's context is chosen by the plan's author  (P2)
Every lens sees "the ECP plus the files in `allowed_paths`", and Elephant wrote `allowed_paths`. The root-cause lens
is asked to "name the real location" of the bug, but it can only look inside the files Elephant chose. If Elephant
picked the wrong place, the reviewer cannot see the right one. Zero context about *how* the plan was made is honest,
but what the reviewer can *see* is still set by the author. So the independence is partial.
**Correction:** a plain program builds the reviewer's view without consulting Elephant. It starts from the request or
finding's file and its importers and callers, and it is defined per lens: comprehension and readiness see the ECP
**only** (a real stranger test); root cause and impact also see the neighbourhood. Files that were cut for size are
listed, so a reviewer can say "insufficient context" instead of judging from part of the code.

### B4. Discovery and verification contradict each other  (P4)
Discovery turns failing tests into findings (evidence level 3, top rank). Verification then demands the **whole suite**
pass. On any repo with more than one failing test, fixing one of them is always rolled back. The system's
best-evidenced findings are exactly the ones it can never complete.
**Correction:** verification is judged against the baseline: no test that passed before now fails, plus the proof
obligation from B2. "Everything green" is not the criterion.

### B5. The trust boundary is drawn around the wrong component  (P5)
The design treats **Pi** as the untrusted part and fences it (guard, Podman). The most untrusted input is the
**target repository's code**, and it runs unfenced on your machine in prepare (`pip install -e .` runs `setup.py`),
discovery (coverage runs the tests), verification and the fail-before check. Repos are also cloned under the evolve
folder, next to the `.env` holding the Azure key.
**Correction:** one boundary: everything that executes repository code runs in the same sandbox as Pi, and working
copies live outside the evolve folder.

### B6. "Enforced, not requested" still has default-allow holes  (P5)
The guard blocks out-of-scope writes, but allows every other tool it doesn't recognise, allows reads anywhere
(including `../../.env`), follows symlinks a cloned repo can contain, and compares paths case-sensitively on Windows
(`.GIT/` is not `.git/**`).
**Correction:** the guard allows only named tools, scopes reads to the repo, resolves real paths, and folds case
where the file system does.

### B7. AI judgement runs before plain checks finish deciding  (P1)
The post-change Goldfish review runs even when the tests failed, scope was violated, or Pi errored, so an AI opinion
is gathered on a change that plain programs have already rejected. The lifecycle also marks an ECP `verified` when
verification **failed**.
**Correction:** deterministic gates first, AI review only for changes that passed them. `verified` records the result.

### B8. Elephant is the long-memory role but forgets within one ECP  (P8)
During revision Elephant sees only the current round's blocking findings, not earlier rounds or its own earlier
versions. A plan can fix A, bring back B, and loop until rejected. Recurring findings are never recognised as recurring.
**Correction:** Elephant's revision input includes every finding across rounds and its earlier versions. The engine
treats a finding that returns after being "fixed" as non-convergence and stops early.

### B9. The ECP holds the plan but not the "after" its definition promises  (P7)
Your definition says the ECP justifies, executes, verifies, evaluates and retains knowledge. In the code, the ECP file is
the plan only. The outcome is a one-line entry in `history.jsonl`, and what started the change (finding, request) is
only in the evidence log. The "learning" Elephant reads is that one-line list.
**Correction:** the ECP carries its origin (finding or request) and, when decided, an outcome section: result, the
check that failed, and the lesson. This sits outside the locked plan part, but in the same file. History becomes an
index of ECPs, not a separate memory.

### B10. Open question: is a one-shot builder right?
The plan is locked, and that is correct. But the implementation is also one-shot: Pi has no shell, so it writes the
fail-before test without ever running it, and any failure rolls the whole thing back. Locking the **plan** does not
require a one-shot **implementation**. Whether Pi may retry within the same locked ECP (seeing only the test result,
still with no shell) is a design decision for you, not a fix. It changes the success rate a lot more than anything
else here.

---

## Parked: additions, not corrections

The rest of CHANGES.md adds capability rather than correcting the design. It stays parked unless you decide otherwise:
PR or merge modes, a records branch, metrics page, SSE, semgrep and the detector registry, reproduce step, auto-pick,
LLM gateway, call ledger, structured outputs, retries and concurrency, a resumable engine, diff coverage, static
regression gate, flake control, venv naming, typed events, and the Goldfish eval harness. The eval harness is how you
would *prove* Goldfish works, which matters for the research claim, but it is measurement, not architecture.

### Phase 2 added some things that are features, not corrections

| Added | Correction or addition? |
|---|---|
| Governance store outside the repo (C11) | Correction (A1) |
| Risk from rules on plan and diff, Elephant advisory (C14–C16) | Correction (A3) |
| Lock checked against the record and disk; approval names the plan hash (C17, C18) | Correction (A2) |
| "Bug fix needs a test in scope" plan check (part of C20) | Correction (supports P4) |
| Autonomy levels 0–3, `human_gates`, `required_lenses` (C12, C13) | **Addition** |
| `max_files` and the other plan scope limits (rest of C20) | **Addition** |
| `Evidence-Head` git trailer (C19) | **Addition** (hardening beyond the stated principle) |
