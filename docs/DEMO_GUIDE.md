# Demo guide

Run all three: `evolve demo all` (or one at a time: `evolve demo 1_flawed_plan_caught`).
Each creates a fresh copy of a small inventory API in a temp folder, with one planted bug
(`remove_stock` lets stock go below zero). The AI replies are scripted, so results are the same every time.

After a run, look inside the demo repo: `evolve show ECP-0001 --repo <path>` prints the ECP and its evidence trail.

## Scenario 1: A bad plan is caught before any code exists

Story: Elephant's first plan fixes the symptom in the API layer.
What to point at: three Goldfish reviewers object (Root cause is a **blocker**). Elephant revises the plan to fix
`Store.remove_stock` and require tests. Round 2 is clean. The plan locks, Pi builds, tests pass, change is merged.
Message: *independent review improved the plan before anything was built.*

## Scenario 2: The builder tries to go out of bounds

Story: while fixing the bug, the (scripted) agent also tries to rewrite `inventory/api.py` and `.evolution/rules.md`.
What to point at: two lines `GUARD BLOCKED ...`. The legitimate edits still go through and the change is accepted.
Message: *scope is enforced, not just requested; the agent cannot edit its own rules.*

## Scenario 3: A change that breaks things is undone

Story: an enhancement (low-stock report) where the agent also quietly breaks `add_stock`.
What to point at: `Verify: tests FAILED`, then `ROLLED BACK`. The repo is byte-for-byte what it was.
Message: *wrong changes never survive; nothing to clean up by hand.*

## Scenario 4: A fix with no proof is refused

Story: the agent fixes the bug but adds no test. Result: ROLLED BACK, reason "no proof". A bug fix must come with a test that fails on the old code.

## Scenario 5: A human must approve a high-risk plan

Story: the plan is marked high risk. The run pauses (in the terminal, or an Approve/Decline banner on the dashboard) and continues only after a human says yes.
Run it with `evolve dashboard --demo`.

## One-sentence summary for your mentor

"An ECP turns a change request into a locked, reviewable plan; Goldfish reviewers with no shared memory try to break
it; Pi builds inside enforced limits; tests and a second model verify; the result is kept or undone, and the record
makes the next plan better."

## Honest limits to mention

- The demo replays scripted AI answers. Live AI planning/review and the live Pi agent need API keys (see TESTING.md).
- Goldfish findings are opinions from AI models; the hard guarantees come from the guard, tests, scope check and rollback.
