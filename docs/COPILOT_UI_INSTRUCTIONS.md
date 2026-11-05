# Instructions for Copilot: rebuild the Evolve dashboard UI

You are changing the screen of an existing tool. You are not changing what the tool does.
Read this whole file first, then work through the phases in order. After each phase, run the checks in section 9 and stop if anything fails.

---

## 1. What the project is (so you do not break it)

Evolve is a spec-governed system that changes software in a controlled way.

- **ECP** (Evolution Candidate Profile): the plan, a YAML header plus Markdown. It is hash-locked when approved. Evidence is append-only and hash-chained.
- **Elephant**: a long-context planner. It plans. It never writes code.
- **Goldfish**: independent reviewers with zero context. Each has one lens. They run in parallel.
- **Pi**: the coding agent. It works inside a scope guard.
- **Control plane**: plain code that decides (gate, verify, decision). No AI makes these decisions.

The dashboard only **shows** this. It never decides anything.

## 2. Hard rules (do not break these)

1. Do **not** change the behaviour of `evolve/engine.py`, `evolve/goldfish/*`, `evolve/verify.py`, `evolve/gate*`, `guard/`, `sandbox/` except for the small, optional event fields listed in section 8. Adding fields to events is allowed. Removing or renaming existing event kinds or fields is **not** allowed.
2. Keep the dashboard as **plain HTML, CSS and JavaScript in `evolve/dashboard/index.html`**. No framework, no build step, no new runtime dependency, no CDN scripts.
3. Keep `const EVOLVE_TOKEN = "__EVOLVE_TOKEN__";`. The server replaces it. Keep sending the `x-evolve-token` header on every POST.
4. Do not call external services from the page. No fonts from the internet. Use `Inter` if installed, and fall back to the system font stack already in `:root`.
5. No emoji anywhere in the UI or in code comments.
6. All existing tests must still pass (section 9).
7. Text in the UI is plain, sentence case, simple English.
8. Work in small steps. After each step the page must still load and a run must still display.

## 3. The problem to fix

In a live demo the screen jumped by itself: first to Elephant, then to the ECP, then to Goldfish. It was hard to follow. The causes in the current code:

1. `render()` ends with `st.scrollTop = st.scrollHeight` while a run is going (`S.follow`). Every new event scrolls the page to the newest section.
2. `render()` rebuilds the whole stage with `st.innerHTML = html` each time the HTML string changes. Scroll position, open sections and hover are lost.
3. The run is one long vertical column of sections (`secScout`, `secPlan`, `secReviews`, `secGate`, `secPi`, `secVerify`, `secDecision`). Later steps are only reachable by scrolling.
4. Goldfish cards only exist after `review_start`, so the screen changes shape mid-run.
5. Discovery claim checks run one at a time, so it looks like there is only one Goldfish.

**The rule that fixes all of it: the layout never moves. Only the contents of fixed places change.**

## 4. Target layout

```
+--------------------------------------------------------------------------------+
| Evolve  repo / branch                 Azure ok   Running, round 1 of 3   [Stop] |
+-----------+------------------------------------------------+-------------------+
| Runs      |  Title of the run                    ECP-0001  |  Agent drawer     |
| Findings  |  One status sentence                           |  (hidden until a  |
|           |  +------------------------------------------+  |  chip is clicked) |
|           |  | Pipeline: fixed height, fixed chips      |  |                   |
|           |  +------------------------------------------+  |                   |
|           |  +------------------------------------------+  |                   |
|           |  | ECP YAML with findings pinned to lines   |  |                   |
|           |  +------------------------------------------+  |                   |
+-----------+------------------------------------------------+-------------------+
| Log (follows live, can pause)           | Who ran when (timeline)              |
+-----------------------------------------+--------------------------------------+
```

CSS grid for the page body:

- Columns: `248px minmax(0,1fr) 0` normally; when the drawer is open: `248px minmax(0,1fr) 340px`.
- Rows: top bar `52px`, body `1fr`, dock `210px` (collapsible with the existing "Hide" button).
- Only the centre column, the left panel and the log scroll, each on their own. The page body does not scroll.
- Below 1100px wide: left panel collapses to a toggle button; the drawer opens over the centre.

## 5. Visual system

Keep the current light look. Tidy it. Replace the `:root` tokens with these (keep any token that other CSS still uses; map old names to new values rather than deleting them):

```css
:root{
  --bg:#fafaf9; --surface:#ffffff; --line:#e8e8e5; --line2:#f0f0ee;
  --text:#16181d; --mute:#6b7280; --dim:#9aa1ab;
  --accent:#2f4bd8; --accent-bg:#eef1fd;
  --good:#1f8a5b; --good-bg:#eaf5ef;
  --warn:#b7791f; --warn-bg:#fbf3e1;
  --bad:#c2372d;  --bad-bg:#fbeceb;
  --elephant:#8b78e6; --pi:#0e7c66;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --sans:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
```

- Sizes: 12px meta, 13px body, 15px panel titles, 20px run title. Sentence case. No all-caps labels.
- One radius for panels (12px), one for chips and buttons (8 to 10px). 1px borders. No heavy shadows.
- Use rows and dividers inside a panel. Do not nest cards inside cards.
- Red is only for blockers and Stop. Blue is only for the primary action and the "working" state.
- Motion: one gentle pulsing dot for "working" (reuse the existing `ping` keyframes). The timeline "now" line moves smoothly. Nothing else animates. Wrap animation in `@media (prefers-reduced-motion: no-preference)`.
- Every state has a word as well as a colour. Keyboard focus is visible (`:focus-visible` outline). Chips are `<button>` elements.

## 6. Behaviour rules

1. **Never** assign `scrollTop = scrollHeight` on the page or centre column. Delete the `follow` scroll in `render()`. Keep `st.scrollTop` exactly as it was.
2. **Update in place.** Build the shell once (top bar, left panel, centre, drawer, dock). On each refresh, change only the elements whose data changed (class, text, width). Give every chip, log line, timeline bar and findings row a stable `id` or `data-key`. Rebuild an area only when the run or the repo changes.
3. Nothing is inserted above or between existing content. The pipeline has a fixed height. The ECP block has a fixed place.
4. The screen never opens the drawer by itself. A blocker turns its chip red and shows in the status line; the person decides when to look.
5. Keep UI state across updates: drawer open/closed and tab, log filter, folded sections, panel scroll.
6. The log has a "Following" toggle that scrolls **only the log box**. It turns off if the person scrolls up in the log. It never affects the page.
7. Replay (`openRun`, `play`, slider) uses the same screen and the same chips. It must not switch to another layout.
8. The status line is always visible and plain: "Running, round 1 of 3", "Waiting for your approval", "Finished: accepted".
9. Errors say what happened and what to do next. Never show a bare code.

## 7. Phases

Do one phase at a time. Keep the page working after each.

### Phase 1: stop the jumping (frontend only)

Files: `evolve/dashboard/index.html`.

1. In `render()`, remove the block that sets `st.scrollTop = st.scrollHeight` (the `follow` branch). Keep the line that restores `keep`.
2. Remove `S.follow` usage for scrolling. Keep the property if other code reads it, but make it control only the log.
3. Replace the stacked `secScout/secPlan/secReviews/secGate/secPi/secVerify/secDecision` column with the fixed **pipeline** block described in 7.2. Keep those functions only if you reuse their inner HTML for the drawer or the ECP area.
4. Change `render()` so it builds the shell once and updates parts by id. Keep the existing `S.paint` idea, but keep a separate cached string per area (`S.paint.pipe`, `S.paint.ecp`, `S.paint.left`, ...) so one area changing does not rebuild the others.

Done when: during a run the page and the centre column do not move unless the person scrolls.

### Phase 2: pipeline, ECP view, left panel

**Pipeline (fixed height, about 220px).** An inline SVG or absolutely positioned HTML. Nodes left to right:

1. `Elephant`
2. A dashed group labelled "Goldfish reviewers" with one chip per lens of the **before** round
3. `Gate`
4. `Pi builds`
5. `Tests and scan`
6. A dashed group "Goldfish again" with one chip per lens of the **after** round
7. `Decide`

Chip states (class names): `wait`, `working`, `clean`, `finding`, `blocker`, `error`. A chip shows the lens name in plain words and a short result ("clean", "2 findings", "1 blocker", "working", "waiting").

Plain-word names for lenses (use in the UI; keep internal names in the drawer):

| Lens | Plain name |
|---|---|
| comprehension | Clarity |
| intent_fit | Fits the request |
| verification | Proof |
| scope_rules | Scope |
| readiness | Readiness |
| root_cause | Root cause |
| impact | Impact |
| behavior_preserved | Behaviour kept |
| conformance | Matches the plan |
| correctness | Correctness |
| security | Security |
| performance | Performance |
| claim | Claim check |

**Roster known before the run reaches it.** Use the backend `roster` events when present (section 8). Until then, fall back to a client-side map that mirrors `ROSTER` in `evolve/goldfish/runner.py`:

- before, always: comprehension, intent_fit, verification, scope_rules, readiness
- before, by type: bug adds root_cause; feature and enhancement add impact; refactor adds behavior_preserved
- after, always: conformance, correctness
- after, by type: feature and enhancement add security; enhancement adds performance

Read the change type from the ECP view that `engine.py` already emits in `elephant_done` (see `_ecp_view`). Before the plan exists, show Elephant plus one placeholder in each Goldfish group labelled "set when the plan is ready".

Lines between nodes: solid once passed, dashed until reached. Chips update by id; do not redraw the whole SVG.

**ECP view.**
- YAML only, with line numbers, monospace, fixed place under the pipeline. Reuse `yamlLines()` and `ecpDoc()`.
- Findings are pinned to the line they refer to. If a finding has a `location` matching a line or a quoted value in the YAML, add a small note directly under that line (red left bar on the line, a bordered note below it). If it cannot be matched, list it in a short "Other findings" list under the block.
- Header: `ECP-xxxx.yaml`, a "locked after approval" tag with the short hash once `locked` arrives, and the version when Elephant revises.
- Footer: counts of blockers, majors and notes, and one sentence on what happens next.

**Left panel (248px).**
- "Runs": current run first with a status dot; click opens replay (`openRun`).
- "Findings from discovery": rows with a severity bar, the title and `file:line`. Keep the "Evolve this" action (`data-fid`) on the row.
- Plain rows, no cards.

**Top bar.** Logo, "Evolve", repo and branch, Azure status, run status, then **Stop**, **Discover**, **Evolve**, Settings. Only Evolve is a filled button. Stop is outlined red and disabled when nothing is running.

### Phase 3: the agent drawer

1. Clicking a chip opens the right drawer (340px). Clicking again, pressing Escape, or a close button closes it. The centre column resizes; nothing else moves.
2. Drawer content for a Goldfish chip:
   - Name, plain lens name, state tag, one sentence from `LENS[name]` (already in the file).
   - **What it was given**: the file list.
   - **What it never saw**: "Your request and Elephant's reasoning."
   - **Findings**: severity, claim, location, suggested fix.
   - **Quote check**: "Quote found, file line N" or "Quote not found, shown as a note" (when the field exists).
   - Time, model, tokens, retries (show only what exists).
   - Tabs: Summary, Raw prompt, Raw reply (hide the last two when the data is missing).
3. Drawer content for Elephant, Pi, Tests and Gate: the same shape using what the events carry (Pi actions and blocked paths, test steps and notes, gate reasons).
4. Clicking a chip also filters the log to that agent, with a visible "Show all" chip to clear it.

### Phase 4: control and insight

1. **Stop** button: calls `POST /api/stop` (section 8). Ask for no confirmation dialogue; show "Stopping..." then the finished state.
2. **Timeline** in the dock (right half): one lane per agent, a vertical "now" line, bars from the start event to the done event, a growing bar while working, a red diamond at the time a blocker was found. Build it from `goldfish_start/done`, `elephant_start/done`, `pi_start/done`, `verify_start/done` event times (`e.t`).
3. **Graphs** (optional view in the dock, toggle "Timeline / Graphs"): agents running over time, tokens by agent (stacked area), open findings (step line). Use inline SVG. Tokens only when `tokens` exists on events; otherwise hide that graph.

### Phase 5: polish

1. Responsive layout and keyboard use (Tab to chips, Enter opens, Escape closes).
2. `prefers-reduced-motion` respected.
3. Error and empty states: say what happened and what to do ("No findings yet. Click Discover to look for problems.").
4. Optional: replace the 1-second polling in `refresh()` with Server-Sent Events from the server. Keep polling as the fallback.

## 8. Small backend additions (all optional for the UI; the UI must work without them)

Add fields only. Never rename or remove existing ones.

| Change | File | Detail |
|---|---|---|
| Roster up front | `evolve/engine.py` | Keep `review_start`. Also emit `roster` once the plan exists: `emit(repo, "roster", before=[...names], after=[...names], change_type=...)` using `goldfish.roster(ecp.change_type, "before"/"after", required)`. |
| What each Goldfish saw | `evolve/goldfish/runner.py` | In `goldfish_start` add `round` and `files=[...]` (the names placed in the sealed context folder by `build_context`). |
| Raw prompt and reply | `evolve/goldfish/runner.py` | In `goldfish_done` add `reply` (the raw model text) and `prompt_chars`. Cap long text (for example 20000 chars). Do not add secrets. |
| Tokens | the LLM client used by `llm.complete` | Capture prompt and completion token counts from the API response when present and add `tokens` to `goldfish_done`, `elephant_done`. Leave out when unknown. |
| Quote check result | `evolve/goldfish/runner.py` | When a finding quote is checked against a file, add `quote_ok: true/false` and `quote_line` to the finding dict. |
| Stop route | `evolve/dashboard/server.py` | Add `POST /api/stop` (same token check as the other POST routes). Add `Runner.stop()` that calls `self.proc.terminate()`, waits a few seconds, then `kill()`. Return `{ok:true}`. Emit `emit(repo, "stopped")` so the UI can show it. `Runner` already runs the job as a child process, so this is safe. |
| Parallel claim checks | `evolve/discover.py` | Optional: run the claim checks in a `ThreadPoolExecutor` so several Goldfish are active at once. Keep the same events and the same results order. |

The UI reads new fields defensively: `d.files || []`, `d.reply || ""`, `d.tokens || null`.

## 9. How to check your work

Run after every phase:

```bash
python -m pytest -q tests/test_dashboard.py tests/test_live.py tests/test_live_e2e.py tests/test_live_paths.py
python -m pytest -q
```

Before renaming or removing any element id, class or text, search the tests for it:

```bash
grep -rn "livedot\|livetxt\|azstat\|settingsbtn\|stage\|dock\|log\b" tests/test_dashboard.py tests/test_live_e2e.py
```

Keep those ids working, or update the test in the same change and say why.

Manual check: start the dashboard and run a scripted scenario (see `tests/test_live_e2e.py` for how scenarios are fed). While it runs, confirm:

- [ ] The page never scrolls by itself.
- [ ] All Goldfish chips are visible before they start.
- [ ] Clicking a chip opens the drawer and nothing else moves.
- [ ] A finding appears under the right YAML line.
- [ ] The log follows live and pauses when you scroll it.
- [ ] Replay uses the same screen as a live run.
- [ ] Stop ends the run and the page shows it.
- [ ] Every state has a word as well as a colour.
- [ ] The page works at 1280px wide and does not break below 1100px.

## 10. Writing rules for UI text

- Say what is happening in plain words: "Goldfish are reviewing the plan. 3 of 6 have started."
- Buttons say what they do: "Send back to Elephant", "Override as owner", "Stop run".
- Use the same word for the same thing everywhere: finding, blocker, run, plan.
- Empty states invite an action. Errors say what happened and what to do next.

## 11. Out of scope

- Changing how Elephant, Goldfish, Pi or the control plane decide anything.
- New frameworks, bundlers or packages.
- The boundary feature (mutable and immutable lists, Boundary Goldfish, `denied` outcome). Leave room for it: the ECP view should render any `mutable` and `immutable` lists it finds in the ECP view data, and a lens chip can be added to the roster later without layout changes.

## 12. How to report your work

After each phase, write a short note:

1. What you changed, by function name.
2. Which tests you ran and the result.
3. Anything you could not do, and why.

Do not claim a phase is done if a test fails or the manual checks above do not pass.
