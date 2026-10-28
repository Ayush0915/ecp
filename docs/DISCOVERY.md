# Discovery: finding what is worth evolving

Before discovery, a person typed the change request. Now the system can find candidates itself, **with evidence**, and
hand one to the normal flow. Discovery changes no code.

```
repo ─► signals (plain programs) ─► Elephant scout ─► checks ─► score + rank ─► findings (files)
                                                                                   │ you pick one
                                                                                   ▼
                                            Elephant plans the ECP ─► Goldfish ─► Pi ─► verify ─► decide
```

## Run it
```bash
evolve discover --repo https://github.com/owner/repo
evolve run --repo <folder> --finding F-001
```
or in the dashboard: connect the repository, press **Discover problems**, then **Evolve this** on a finding.

## Step by step
1. **Signals** (plain programs, no AI). Python repos in v1:

   | Metric | Source |
   |---|---|
   | correctness | the repo's own failing tests |
   | security | bandit, plus a built-in secrets scan |
   | quality | ruff, built-in TODO/FIXME scan, git hotspots (needs 5+ commits) |
   | testing | coverage (files under 80%, with the lines never run) |
   | performance | slow tests (over 0.2s) from pytest timings |

   A tool that is not installed is shown as **skipped** with the install command. Nothing fails because of it.
2. **Scout.** Elephant gets the signals, the flagged code, `rules.md` and past ECPs and lists real problems, each with
   a quoted line, impact (with a reason) and fix size. It never writes code.
3. **Checks.** A plain program confirms the quoted line is really in that file. Then a zero-context Goldfish gets only
   the claim and the file and says whether it is supported. Anything that fails is dropped, and the reason is kept.
4. **Score and rank.** Evidence is computed by a program, not by the model:
   - 3 = reproduced (a test fails on the current code)
   - 2 = a tool of the *same metric* flagged that exact spot
   - 1 = AI reading only (the quote exists, nothing else backs it)

   Ranked by evidence, then impact, then fix size (small first). Performance findings with evidence 1 are labelled
   **unproven**: no measurement links that code to the slowness.
5. **Pick.** You choose a finding (dashboard button or `--finding`). Its text becomes the request, the ECP records which
   finding started it, and the finding file is updated with the outcome.

## Files it leaves in the repo
- `.evolution/findings/F-001.json` ... one per finding: metric, evidence, signals, quote, status, linked ECP
- `.evolution/baseline.json`: which detectors ran or were skipped, and the signals
- `.evolution/discovery.jsonl`: append-only log of every discovery run, including what was dropped and why

## Honest limits
- v1 supports Python repos. Other languages still get the test baseline, secrets and TODO scans.
- Evidence 2 means a tool flagged the spot, not that the AI's explanation is right. The fail-before proof still decides bug fixes.
- Impact and fix size are AI judgement, shown with a reason.
- The independent claim check uses the same provider as the other reviewers, so blind spots can overlap.
- A human still picks the finding. Choosing automatically is future work.
