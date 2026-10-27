# Demo script (about 10 minutes)

Before the mentor arrives: `evolve doctor --ping` says **Ready.**, the repo is committed and its tests pass, and
`evolve dashboard` is open in the browser. Keep `evolve dashboard --demo` ready as a fallback.

## 1. Say the idea in 30 seconds
"AI can change code faster than people can check it. So I put a written plan, the ECP, between the request and the code.
Elephant writes the plan. Goldfish reviewers, who never saw how the plan was made, try to break it. The plan is locked.
Pi builds, but only inside the files the plan allows. Tests and a second look check the result. Then it is kept or undone,
and everything is recorded."

## 2. Run a real request (live)
Type a bug or a small feature in the box. Press **Run governed evolution**. Narrate as the steps light up:
1. **Plan**: "Elephant wrote this document. Intent, which files may change, how we will know it worked. No code yet."
2. **Review**: "Each card is a separate reviewer with no shared memory. Red means it found a problem." If a plan gets revised, open the **Plan versions** tab: "The plan itself evolved. Red lines removed, green lines added, because of what reviewers found."
3. **Build**: "Pi works in a locked container. Every file it touches is logged. A red BLOCKED line means it tried to leave its scope and was stopped."
4. **Verify**: "Plain programs, no AI: tests, scope check, and for bugs, proof that the new test fails on the old code and passes on the fix."
5. **Decide**: "Accepted and merged, or undone completely. Low risk is automatic; high risk waits for a human." (If it pauses, press **Approve**.)

## 3. Show the evolution is real
- **Story tab, code diff**: the exact lines that changed.
- **Repo files tab / terminal**: `cat .evolution/ecps/ECP-0001.md` (the YAML plan), `git log --oneline` (the merge), the evidence log.
- **Replay**: press Replay and the run plays back step by step from its saved record.
- **Second request**: run another change. "It reads what happened last time (Memory panel)."

## 4. Why governance matters (2 minutes)
Press **Compare: plain vs governed** with the same request. Walk the table: the plain agent had no plan, no review, no limits, and
may have touched files the task never needed. "Same request, same repo. The difference is the process."

## 5. Be honest about limits
- Goldfish reviewers are AI opinions; with one model their blind spots overlap with the builder's. The hard guarantees are the lock, guard, tests, the fail-before proof and rollback.
- It needs a repo with tests; the tests are the judge.
- Today a human or a trigger (`--from-failing-tests`, `--from-file`) starts a change. Fully self-initiated evolution is the next step.

## If something goes wrong live
Switch to `evolve dashboard --demo`, pick `1_flawed_plan_caught`, press Run. Same story, scripted, no network.
