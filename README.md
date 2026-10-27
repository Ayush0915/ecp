# ECP-Governed Evolution

**Change software safely, with AI, under rules a human can read.**

You ask for a change (fix a bug, add a feature, improve something). Instead of an AI
editing your code directly, the change goes through a governed process:

```
 1 PLAN        2 REVIEW        3 BUILD       4 VERIFY        5 DECIDE
 Elephant  ->  Goldfish   ->   Pi agent ->   Tests + Goldfish -> keep or roll back
 writes the    independent      edits only    check the         (human approves
 ECP           reviewers        declared      result            if high risk)
               challenge it     files
```

Everything is held together by one document, the **ECP (Evolution Candidate Profile)**:
the plan, the rules, the success criteria, and the record of what happened.

## The three characters

| Name | Job | Key rule |
|---|---|---|
| **Elephant** | Remembers the whole repo, the rules and past changes. Writes the ECP. | Never writes code. |
| **Goldfish** | A team of reviewers. Each has *zero memory* of how the plan was made and checks one thing. | Sees only the ECP and the files it names. |
| **Pi** | The coding agent. Builds exactly what the approved ECP says. | Cannot touch files outside the ECP's scope. |

Goldfish is the strength of the idea: a plan is only trusted after reviewers who were not
part of making it have tried to break it.

## Try it (no AI key needed)

```bash
pip install -e ".[dev]"
evolve demo list          # shows the demo scenarios
evolve demo all           # runs them all in the terminal
evolve dashboard --demo   # the same, on a live web page you can click through
pytest -q                 # runs the tests (80)
```

The demo uses *scripted* AI replies so it is repeatable and free. See `docs/DEMO_GUIDE.md`
for what each scenario proves and how to talk through it.

## Use it on your own repo

```bash
cd my-repo            # must be a git repo with tests
evolve init           # creates .evolution/ and detects your test command
evolve run "Bug: users can submit an empty name"
```

See `docs/USE_ON_ANY_REPO.md` (needs an AI key and, for real building, the Pi agent).

## Run it live (Azure OpenAI GPT-5.5 + Pi in Podman)

```bash
evolve sandbox build
evolve init --profile azure-podman       # inside the target repo
evolve doctor --ping                     # must say Ready.
evolve run "Bug: ..." --open-report      # also accepts --repo <path or git URL>
```

Full checklist: `docs/LIVE_SETUP.md`. Every run produces an HTML report for showing the process.

## The dashboard (this is what you show)

```bash
evolve dashboard                 # on the real repo (after init); open http://127.0.0.1:8765
evolve dashboard --demo          # practice mode: scripted scenarios, no key needed
```

One page shows the five steps lighting up, the plan, each Goldfish reviewer as a card, every file Pi touched
(blocked ones in red), the tests, the code diff and the final decision. You can **replay** any run step by step,
see the **plan change from version 1 to version 2**, approve high-risk plans with a button, and press
**Compare: plain vs governed** to run the same request with an unrestricted agent next to the governed run.
Everything on the page is also plain files in `.evolution/` that you can open.

## Other commands

| Command | What it does |
|---|---|
| `evolve compare "request"` | Same request, plain agent vs governed, side by side |
| `evolve run --from-failing-tests` | Trigger: failing tests become the change request |
| `evolve run --from-file issue.md` | Trigger: use an issue file as the request |
| `evolve plan-diff ECP-0001` | Show how the plan changed between versions |
| `evolve show ECP-0001` | The plan plus its evidence trail |
| `evolve doctor [--ping]` | Is this machine ready? |

## Read next

- `docs/DEMO_SCRIPT.md` : what to say and click, step by step

- `docs/LIVE_SETUP.md` : the mentor-machine checklist

- `docs/HOW_IT_WORKS.md` : every part explained simply
- `docs/DEMO_GUIDE.md`   : how to present it
- `docs/USE_ON_ANY_REPO.md` : setup for a real repo
- `docs/TESTING.md`      : what is tested, and what is NOT yet tested
