# Companion Panel, Direction C (Phase 1) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development in the planning session) to implement this plan task by task.

**Goal:** Repaint the Companion side panel in direction C ("one next step") with no change in
behavior: a progress bar on top, the current step as a "Next" card, finished steps as compact lines,
and a quieter header.

**Architecture:** C-lite (owner, 2026-10-11). The step-row DOM stays (`li.stg` rows in `ol#rail`,
the `stg-open-*` / `stg-body-*` ids, `#identity`, `#foot` with `#note` and `#foot-controls`) and is
repainted mostly with CSS. Additions are small and named: one `#progress` region, one icon, and
visually hidden text where a control becomes icon-only. The footer keeps its one primary button.

**Tech Stack:** plain JS IIFEs on `window.careerStudioCompanion`, `extension/panel/panel.css` tokens
(`--cs-*`, role values pinned to `frontend/app/globals.css`), pytest node harness
(`tests/extension_panel_harness.py`), Playwright for a static preview.

**Base:** this plan reads origin/main 022a3fd3. Build only after main is merged into the branch.
Line numbers below are as on that commit; re-check them after the merge.

**Mockups:** the owner's private design canvas ("Companion panel options", artboard C · One next
step, plus the C row of Apply/running/Queue states, which are Phases 2 and 3). Ask the planner for
screenshots before Task 2. Phase 1 draws only what exists today: Job, Resume, Fill, Track.

---

## Goal Card

Goal: The Companion looks like direction C: one clear next step, progress at a glance, less chrome,
and every existing feature, behavior, keyboard path and test-pinned rule kept. It is the first of
three phases (then one job across pages, then the queue), so the new look must leave room for the
run states those phases add to the same Next card.

Principles:
- **No behavior change.** If a change needs new data, a new action or new copy beyond labels named
  here, it is out of scope: stop and ask.
- **Repaint before restructure.** Prefer CSS over DOM changes; every DOM change is listed here.
- **Accessible as drawn.** An icon-only control keeps its words for screen readers; the progress bar
  is announced; focus and the one-open-row model are unchanged.
- **Light and dark** both work (panel.css has a dark override).

Non-goals: Apply/Queue buttons, run states, the This page | Queue switch (Phases 2 and 3).
The options page is not redesigned, only kept intact.

Autonomy: **peer** (GPT-6.1 Sol implements, Opus 5.5 reviews in the browser). Log deviations as
`Deviation: <planned> → <did>, because <reason>`. Never widen scope.

---

## The visual rules (direction C, as they map onto today's DOM)

- **Header (`#identity`).** Title (16px, 600), then "Company · status" on one muted line, the ATS
  rings compact on the right of that block, then job-fact chips when the page's job match carries
  them. Refresh and Open become icon buttons (32px, muted) at the top right; their words stay for
  screen readers and tests (visually hidden span), and their `aria-label`s are unchanged.
- **Progress (`#progress`, new).** Four 4px segments (done = success, active = primary, rest =
  muted), then "Step N of 4 · Name" (12px muted). `role="img"` and an `aria-label` with the same
  words. N and Name come from the ACTIVE row of `railModel` (`decision.stage`), never the reopened row.
- **Rail.** The active row is the "Next" card: low surface, 12px radius, 16px padding, an eyebrow
  "Next" drawn by CSS (`::before`, not text in the DOM), the step name as the card title (17px, 600),
  and the open body inside. Done rows are compact 32px lines: circle-check in success, name, summary.
  Skipped rows read like done rows with the minus mark and "Not needed". Locked rows are not drawn
  (`display: none`); the progress bar says where you are. A reopened done row expands in place as
  today.
- **Footer.** Unchanged structure. The primary (`.cta`) is full width, 36px, primary fill; the
  status segment becomes C's pill segment; Stop is an outline button.
- **Bodies.** Base picks are C's selectable rows (radio look, ring outline when selected); forks and
  the fill-mode segment become pill segments; `.sub` lines stay 12-13px muted. No DOM changes.
- **Tokens only.** No new colors: use the existing `--cs-*` and role containers. 8px grid.

---

## Task 1: A static panel preview for visual review

The panel can't be loaded as an extension in Playwright (Chrome 150 rejects `--load-extension`),
and the node harness has no layout. Reviewers need to SEE each state.

**Files:**
- Create: `backend/scripts/panel_preview.py`
- Create: `backend/tests/fixtures/panel_preview/*.json` (one state per file)

**What it does:** serves `extension/` plus an injected stub of `chrome.*` (reuse the shapes in
`tests/extension_panel_harness.py` `_PANEL_FAKES_JS`, but against the real DOM) so `panel/panel.html`
renders in a normal browser at 380×760. Each fixture is a spec for the stub: the tab url, the
`/api/jobs/match` reply, base scores, application, page detection, fill report. The script prints
one URL per fixture, for example `http://127.0.0.1:<port>/panel/panel.html?state=job-saved`.

States (made-up employers only): `job-unsaved`, `job-saved` (4 bases scored), `resume`,
`resume-ready`, `fill` (form detected), `fill-report` (a finished loop report with each group),
`track-draft`, `track-applied`, `dark-job-saved` (`prefers-color-scheme: dark`).

**Test:** `tests/browser/test_panel_preview.py` loads each state headless and asserts the panel
rendered (no console errors, `#rail` has 4 `li.stg`, the `.cta` text matches the state). Commit
`chore(panel): a static preview of each panel state`.

**Screenshots:** the reviewer takes "before" screenshots of every state now, for the comparison in
Task 6.

## Task 2: The progress region

**Files:** `extension/panel/panel.html` (a `<div id="progress" class="progress">` between
`#identity` and `#rail`), `extension/panel/panel.js` (`renderProgress(decision)` called inside
`withPlaceKept` beside `renderIdentity`), `extension/panel/panel.css`,
`backend/tests/extension_panel_harness.py` (add `progress` to `REGIONS` and `BY_ID`),
`backend/tests/test_extension_panel.py`.

**Failing tests first:**
- a fresh unsaved job renders `#progress` with segments `["active","","",""]` and the label
  "Step 1 of 4 · Job"; `aria-label` equals the label;
- a saved, tailored, applied job reads "Step 4 of 4 · Track" with the first three done;
- reopening the done Job row (revisit) does NOT move the progress bar (it follows `decision.stage`);
- a page with no job (a `chrome://` tab or a blank page) renders no progress bar.

Implement with `node()`/`attach()`; segments are `span.seg-bar` with class `done` or `active`.
Commit `feat(panel): a step progress bar`.

## Task 3: The rail repaint (Next card, compact done rows, hidden locked rows)

**Files:** `extension/panel/panel.css` only, unless a test proves a DOM change is needed.

**Rules to write:** see "The visual rules". The eyebrow is
`.stg.active > .stg-row::before { content: "Next"; … }` so `.stg-row` text stays "1 Job".

**Pins that change on purpose** (update the test, say so in the commit):
- `test_extension_panel.py` ~2518: the `.stg-num` rule keeps `display:flex`, `align-items:center`,
  `justify-content:center` and `line-height:22px` (unchanged). Keep the pin; if the active card hides
  the numeral, do it with a separate `.stg.active .stg-num` rule.
- `~2575`: `.rail { overflow-y: auto; flex-direction: column }` and `.stg { flex: none }` stay.
  Keep them.
- Locked rows: add a pin that `.stg.locked { display: none }` exists, and keep every existing DOM pin
  on locked rows (they stay in the DOM).

**Tests:** all `test_extension_panel*.py` pass unchanged except pins named above. In the preview,
the reviewer compares each state to the canvas's C artboard.
Commit `feat(panel): the current step is a Next card; finished steps are compact`.

## Task 4: The header repaint, icon-only Refresh and Open

**Files:** `extension/panel/icons.js` (add `rotate-cw` from lucide-react 1.48, keeping the ISC line),
`extension/panel/panel.js` (`refreshControl` ~1800 and `deepLinkAnchor` ~1675: icon plus a
`span.vh` holding today's words; `renderIdentity` ~1759 puts `.co` and the status chip on one line,
and job-fact chips only if the match reply already carries the fields (`applyMatch` and the
`/api/jobs/match` `job` summary: location, work mode, employment type, salary); if it doesn't,
skip the chips and log a deviation (no backend change in Phase 1)), `extension/panel/panel.css`
(`.vh` visually hidden; `.identity` layout).

**Pins that change on purpose:**
- `test_extension_panel_icons.py`: the icon list gains `rotate-cw`.
- `test_extension_panel.py` ~921 and ~3203: the `.identity .linkish` and `.identity .refresh` CSS pins
  are rewritten for the icon buttons (keep the disabled and aria-disabled opacity rules).
- ~1668-1722: `.row1` children and the `id-foot` order may change. Rewrite those two pins to the new
  structure; keep every text, href and aria-label pin (`Refresh`, "Refresh from Maestro CS",
  "Open in Maestro CS (opens in a new tab)").
- Refresh `button.text == "Refresh"` still holds (the hidden span). Keep it.

Commit `feat(panel): a quieter header with icon buttons`.

## Task 5: Footer and stage bodies repaint

**Files:** `extension/panel/panel.css` only.
- `.cta` full width, 36px; `.status-seg` as a pill segment; `.stop` outline.
- `.baserow` as selectable rows (ring outline on `.sel`), `.fork` and `.seg` as pill segments,
  `.prog` and `.loop` spacing to C, `.sub` sizes.
- **Options page:** `extension/options.html` links `panel.css`. Open it in the preview server and
  check it still reads correctly (light and dark); fix with options-scoped overrides if not.
- Keep the CSS pins on `.cta` (`white-space:nowrap`, `flex:none`, `min-width`), `.p-foot` column,
  `.note` `overflow-wrap:anywhere`, `.foot-controls` wrapping, `.status-seg button` nowrap.

Commit `feat(panel): footer and step bodies in direction C`.

## Task 6: Visual review, docs, gate

- **Reviewer, in the browser pane:** every preview state at 380×760, light and dark, side by side
  with Task 1's "before" shots and the canvas. Keyboard: Tab through each state; the open-row
  model and focus restore (`withPlaceKept`) behave as before.
- **Docs:** SYSTEM.md §7 Chrome extension (one sentence: the panel draws the rail as a progress bar
  plus one Next card; locked rows are hidden, still in the DOM), `extension/INTERNALS.md` (the
  rail section: same model, new paint; `#progress` region), CHANGELOG (owner voice for the opener),
  `extension/STORE_LISTING.md` (the five screenshots need retaking after the owner's live check).
- **Gate:** `pytest tests/ mcp_server/tests/ -q -n auto --dist loadfile` from `backend/`;
  `python3 scripts/check_system_md.py`; slop ratchet for `extension` and `backend`, named in the
  claim; re-baseline counts only with a reason.
- **Owner live check:** load the unpacked extension in Chrome on three real job pages (one saved
  with scores, one tailored, one application form) and look.

Commit `docs: the Companion's direction C look`.
