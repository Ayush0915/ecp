# Next phase: making the core true

Written 30 Oct 2026. Builds on [CORE_CORRECTIONS.md](CORE_CORRECTIONS.md) (where the design contradicts itself), the
research survey (what Meta, Google, GitHub, DeepMind and recent papers show), and the Goldfish target design (deck:
*Goldfish Architecture*). Phases 1–2 are done: 200 tests pass, uncommitted.

**The rule for this phase:** every item makes an existing principle true. Nothing adds a new capability. If a task
starts growing a dashboard, an integration or a new detector, it belongs in "Parked" at the end.

**The claim this phase must let you test:**
> Zero-context reviewers whose view is built by a program, and whose findings must be grounded to block, catch real
> problems with fewer false blocks than reviewers that share the planner's context, and the builder never grades its own work.

---

## Decisions to confirm with your mentor first

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 | For bug fixes, the reproduction test is written **before** the fix and locked | Yes | The builder stops writing its own exam (Agentless, Meta's assured test generation) |
| D2 | Only **grounded** Goldfish findings can block | Yes | LLM reviewers hallucinate and nitpick; uReview, CriticGPT and Adversarial Review all show grounding/filtering is what makes them usable |
| D3 | "Verified" and "landed" are separate; a human lands the change | Yes | Every big-company system we looked at keeps a human between an agent's change and the main branch |
| D4 | Pi may retry the **implementation** within the same locked plan, seeing only test output | Yes, at most 2 attempts | The plan stays locked; with a locked failing test to aim at, a retry costs little and changes success rate most |
| D5 | The change type is fixed **before** Elephant plans | Yes | The agent being judged must not pick the label that sets its own scrutiny |

If any answer is "no", strike the matching milestone below; the others still stand.

---

## Milestones

Effort assumes you working with coding agents, a day being a focused working day. Every milestone ends with its tests
passing and the full suite green.

### M0. Checkpoint and sign-off · 1 day
- Commit Phases 1–2 as one checkpoint (message in the last summary).
- Walk your mentor through the Goldfish deck and CORE_CORRECTIONS; get D1–D5 answered.
- **Done when:** a clean commit exists and D1–D5 have answers.

### M1. Type fixed up front, proof obligations by type · 2 days · fixes B1, B2 · needs M0 (D5)
| Change | Where |
|---|---|
| The engine receives the change type from its origin: a finding's `change_type`, `--type` on the CLI, or a choice in the dashboard. Elephant still writes `change_type`, but a mismatch is recorded as `type_disagreement` and raises risk; it no longer changes the roster or the proof | `engine.py`, `cli.py`, `dashboard/server.py`, `elephant.py` (prompt) |
| New `proof.py`: one fixed obligation per type, checked by plain programs. **bug**: fail-before / pass-after (existing check). **refactor**: tests that existed at the base commit are byte-identical and pass. **enhancement/feature**: at least one new or changed test, it passes, and no assertion line was removed from an existing test | new `evolve/proof.py`, `verify.py` |
| The ECP's own `verification` list can add checks but never replace the obligation | `verify.py` |
- **Tests:** an ECP relabelled `enhancement` for a bug finding still runs root-cause and the fail-before proof; a refactor that edits an existing test is rolled back; an enhancement that deletes an `assert` is rolled back.

### M2. Locked reproduction test for bug fixes, bounded retries · 4 days · lessons 2 and D4 · needs M1
| Change | Where |
|---|---|
| After approval, a **reproduce** step: Pi runs with scope limited to test files and is told to write a test that shows the bug, not to fix it | `engine.py`, `pi_runner.py` (scope override, prompt) |
| A program checks the test **fails on the current code** for an assertion reason (junit outcome, as in C04). Its file hashes are written to the evidence as `repro_locked`. If nothing fails, the run ends as `not_reproduced` | `proof.py`, `ecp.py` (evidence kind) |
| The **fix** step: Pi runs with the locked test files added to the forbidden paths | `pi_runner.py`, `scope.py` |
| Verify: the locked test files are unchanged (hash) and now pass. The fail-before proof is structural, no longer reconstructed afterwards | `verify.py`, `proof.py` |
| If tests fail, Pi gets the trimmed test output and tries again, up to `max_build_attempts` (default 2), under the same plan hash. Each attempt is recorded | `engine.py`, `governance.py` (one field) |
| Findings that already have a failing test (evidence level 3) skip the reproduce step: that test is the lock | `engine.py`, `discover.py` |
- **Tests (scripted Pi, two-phase scenarios):** a fix that edits the locked test is blocked by the guard and by verify; a reproduction test that passes on current code ends as `not_reproduced`; attempt 1 fails and attempt 2 passes, so the change is accepted with an unchanged plan hash.

### M3. Judge against the baseline, plain checks before AI · 2 days · fixes B4, B7 · needs M1
| Change | Where |
|---|---|
| Prepare stores the IDs of tests that fail at baseline (pytest `--junitxml`) | `prepare.py` |
| Verify passes when no test that passed at baseline now fails, plus the M1 obligation; "the whole suite is green" is no longer the rule | `verify.py` |
| The after-build Goldfish review runs only when tests, scope, obligation and diff limits all passed | `engine.py` |
| The `verified` status carries the verdict (`passed` / `failed`) | `engine.py`, `lifecycle.py` |
- **Tests:** a repo with one unrelated failing test still accepts a correct fix of another bug; a failed-test run makes zero after-build LLM calls.

### M4. Goldfish v2 · 7 days · the target design · needs M0 (D2)
Build in this order; each step is usable on its own.

| Step | Change | Where |
|---|---|---|
| 4a | **Finding contract.** `Finding` gains `evidence: {kind: quote|field|absence, file, line, text}`. Every lens prompt gets a checklist, a severity rubric with one example each, and the evidence rule | `goldfish/runner.py`, `goldfish/lenses/*.md` |
| 4b | **Grounding check (program).** quote: the text is within ±3 lines in a file that was in this reviewer's folder. field: the ECP field contains it. absence: the thing is really missing (field empty, or file listed as cut in the manifest). Ungrounded becomes advisory. Unavailable/unreadable stays blocking (fail closed) | new `goldfish/grounding.py`, `engine.py` |
| 4c | **Context contracts and neighbourhood.** Each `Lens` declares its inputs; comprehension and readiness get the ECP only. A program builds the neighbourhood from the **origin file** (Python `ast`: importers of it, callers of its functions; grep fallback for other languages), capped. Every folder gets `CONTEXT_MANIFEST.md`; its hash goes to the evidence | `goldfish/runner.py`, new `evolve/neighbours.py` |
| 4d | **Ledger.** Fingerprint = lens + location + normalised claim. Tracks open / fixed / recurring across rounds. Elephant's revise gets the whole ledger and its earlier versions. A blocking finding that returns after being fixed ends the loop as `not_converging` | new `goldfish/ledger.py`, `elephant.py`, `engine.py` |
| 4e | **Disputes.** Elephant may return `disputes: [{fingerprint, rebuttal, evidence}]` with grounded evidence. A zero-context adjudicator gets the finding, the rebuttal and the cited lines and rules uphold or dismiss | `elephant.py`, new `lenses/adjudicator.md`, `goldfish/runner.py` |
| 4f | **Insufficient context.** A grounded "insufficient context: X" where the manifest shows X was cut: add X and re-run that lens once, instead of sending it to Elephant | `goldfish/runner.py`, `engine.py` |
- **Tests:** a finding quoting a line that doesn't exist cannot block; a quote from a file outside the reviewer's folder is ungrounded; comprehension's prompt contains no source file; the neighbourhood of `shop/pricing.py` includes `shop/report.py`; a recurring finding stops at round 2; a dismissed dispute stops blocking and both sides are in the evidence.

### M5. ECP as claims with evidence, and its outcome · 3 days · lesson 6, fixes B9 · needs M1, M4a
| Change | Where |
|---|---|
| `success_criteria` items become `{claim, kind: checked | judged, by}`. Checked claims point at a proof obligation or a check command; judged claims point at the lens that judges them. Plain strings are still accepted and treated as judged | `ecp.py`, `elephant.py` (prompt) |
| Goldfish findings attach to the claim they attack (rebut or undercut), so the record reads claim → evidence → defeaters | `goldfish/runner.py`, `engine.py` |
| At `decided`, an `outcome` block is written into the ECP, outside the hashed plan: result, the check or finding that decided it, residual risks (the open advisory notes), and one lesson line | `ecp.py`, `engine.py`, `decide.py` |
| The `origin` (finding id or request) is a field, not only an evidence line. `history.jsonl` becomes an index pointing at ECPs | `ecp.py`, `decide.py`, `elephant.py` |
| Elephant gets past ECPs that touched the same files, not the whole list | `elephant.py` |
- **Tests:** an old ECP file still loads; the outcome block doesn't change the plan hash; a claim marked checked with no matching obligation or check is a plan problem.

### M6. Verified is not landed · 1.5 days · lesson 5 · needs M0 (D3)
| Change | Where |
|---|---|
| An accepted change stays on `evolve/ECP-x` as one commit, with outcome `verified`; nothing is merged | `decide.py`, `engine.py` |
| `evolve land ECP-x` (and a dashboard button) checks the lock once more and merges. The record says who landed it | `cli.py`, `decide.py`, `dashboard/server.py`, `index.html` |
| Optional governance switch `auto_land_low_risk` (default off) for demos | `governance.py` |
- **Tests:** an accepted run leaves main untouched; `land` refuses if the ECP file changed; the landing user is in the evidence.

### M7. One trust boundary · 4 days · fixes B5, B6 · needs M3
| Change | Where |
|---|---|
| Guard allows only named tools; every read is limited to the repo; real paths are resolved (symlinks); `.git` / `.evolution` match case-insensitively. Same rules in `scope.py` and `guard-core.mjs`, with new shared cases | `guard/*`, `scope.py`, `tests/scope_cases.json` |
| Working copies move to `~/.evolve/workspaces/` (outside the folder that holds `.env`) | `cli.py`, `prepare.py`, `dashboard/server.py` |
| With `sandbox: podman`, everything that runs repository code runs in the same image as Pi: `pip install`, baseline tests, discovery tools, verify, the proof checks | `sandbox/Dockerfile.pi`, new `evolve/sandbox.py`, `prepare.py`, `discover.py`, `verify.py`, `proof.py` |
- **Tests:** symlink escape and `.GIT/` writes are blocked; `read ../../.env` is blocked; with Podman on, a canary file shows no repository code ran on the host.
- Check on the office laptop (WSL + Podman) before the demo; Podman on WSL is the most likely thing to need a fix.

### M8. The experiment · 5 days · lesson 3 · needs M4 (M2 helps)
| Change | Where |
|---|---|
| 20–30 cases: seeded defects in the fixture repos (wrong root cause, missing test, scope creep, injected bug, plus clean controls) and 5–10 Python issues from SWE-bench Verified | new `tests/goldfish_eval/` |
| `evolve eval goldfish --setup A|B|C|D`: A shares the planner's context; B is today's Goldfish; C is the M4 design; D is C on a different deployment | new `evolve/eval.py`, `cli.py` |
| Metrics from the evidence log: blocking precision, recall on seeded defects, ungrounded rate, rounds to converge, tokens | `evolve/eval.py` |
- **Done when:** one results table (4 setups × lenses) exists, with the run settings recorded, and you can say in one sentence whether C beats A and B.
- Cost: roughly (cases × lenses × setups) model calls. Agree the Azure budget with your mentor before the full run; do a 5-case dry run first.

### M9. Docs and demo · 2 days · needs everything above
- Update HOW_IT_WORKS (type fixed up front, proof obligations, locked reproduction, grounded blocking, ledger, verified vs landed), README and the Goldfish deck (add the results slide).
- A demo script for your mentor's repo: discover → pick a bug → reproduction locked → fix → Goldfish review with a grounded finding → verified → you land it → the evidence log.

---

## Order and calendar (about 4 weeks)

```
week 1   M0 ─ M1 ─┬─ M3
                  └─ M6
week 2   M2 ──────── M4a, M4b
week 3   M4c ─ M4d ─ M4e ─ M4f ─ M5
week 4   M7 ─ M8 ─ M9
```

M4 doesn't depend on M1–M3, so if you want results earlier, do M4 and M8 first and the rest after: the experiment
only needs Goldfish v2. That is the faster route to something to show; the order above is the safer route to a
correct system.

## Risks

| Risk | What to do |
|---|---|
| The reproduce step (M2) roughly doubles Pi runs | Skip it for evidence-3 findings; cap attempts; measure cost per ECP |
| Grounding makes Goldfish too quiet (few findings block) | That is a result, not a bug: M8 measures recall; tune the evidence rule, not the veto |
| AST neighbourhood is Python-only | Grep fallback for other languages; state it as a limit |
| Podman on WSL | Test on the office laptop in week 4 day 1, before the demo |
| Azure cost of M8 | 5-case dry run, then agree the budget |

## Parked (not this phase)

PR integration and records branch, metrics page, SSE, LLM gateway and call ledger, structured outputs, retries and
concurrency tuning, detector registry and semgrep, reproduce-from-discovery as a separate command, auto-pick (L3),
resumable engine, diff coverage, static regression gate, flake control, typed events, and the remaining CHANGES.md
items not named above.

## Map from findings to milestones

| Finding | Milestone |
|---|---|
| B1 agent picks its own scrutiny | M1 |
| B2 proof comes from the plan, not the type | M1 |
| B3 Goldfish view chosen by the plan's author | M4c |
| B4 discovery vs verification contradiction | M3 |
| B5 trust boundary around the wrong component | M7 |
| B6 guard default-allow holes | M7 |
| B7 AI judgement before plain checks | M3 |
| B8 Elephant forgets within one ECP | M4d |
| B9 ECP has no "after" | M5 |
| B10 one-shot builder | M2 (D4) |
| Lesson 1: only grounded findings block | M4a, M4b |
| Lesson 2: the builder writes its own exam | M2 |
| Lesson 3: measure independence | M8 |
| Lesson 4: untrusted text must not steer actions | M7, plus M4c (reviewers' view built by program) |
| Lesson 5: verified is not landed | M6 |
| Lesson 6: ECP as claims and evidence | M5 |
