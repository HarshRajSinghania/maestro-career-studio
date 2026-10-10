# Companion queued run: the Companion applies to your Queued jobs, several tabs at once

Approved by the owner on 2026-10-10 (brainstorming session). It extends phase 4 (full automation,
`2026-10-05-full-automation-design.md`), whose rejected option (3) "the Companion submitting" stays
rejected for Submit only. Nothing here is built yet.

## Goal

The Companion already fills one page well (observe, map, pick, verify, attach). The owner wants it
to work as an applying agent: take the jobs you queued, open them a few at a time, fill every page,
move on to the next page by itself, and stop at the review page, so the only thing left for you is
Submit. It should ask you only when it must, and ask in the tab it is working in.

The owner's main complaint with the external bot was friction: it stopped "for small things" because
of the gates. Lowering that friction is part of the goal, not a side effect.

## Owner decisions

- **How far it goes:** the Companion clicks Next / Continue on every page and stops on the review
  page. **Submit stays yours at every setting.** This lifts only the Next half of the 2026-09-26
  rule "Next and Submit stay the user's".
- **Which jobs:** your Queued jobs only. The queue is the consent step; the server applies Companies
  to skip, already-applied and the remaining daily cap before any tab opens. Full automation must be On.
- **Job boards:** follow a posting's Apply out to the employer's own application site. LinkedIn Easy
  Apply and Indeed Apply park as Needs you (their terms forbid automated use).
- **Sign-in:** use the saved job-site login to sign in to an existing account. Creating an account,
  email or SMS codes and CAPTCHAs always park.
- **Where it asks:** in the tab. The tab waits on the field, the panel lists every tab that needs you,
  you answer in the existing pause row, "Remember this answer" saves it, and that tab's run resumes.
- **Friction rules:**
  - A run stops only for the protected list (below) or a wall it cannot pass (CAPTCHA, email code,
    account creation). Everything else is answered, marked, and shown on the review page. Flags warn;
    they never stop a run.
  - One stop per page, not per field: fill everything else first, then park once with all that
    page's open questions together.
  - Questions that can be predicted are asked once, before the run (the "Before we start" sheet).
  - An answer is asked once: saved to the profile, it never parks again.
  - A job with no tailored PDF is quick-tailored (no model call) or falls back to the base PDF.
- **Answer the safer question:** the run gives a safe answer to anything NOT on the protected list.
- **Protected list** (park unless a saved fact answers it): work authorization and sponsorship;
  salary and pay; EEO and demographics (unless EEO consent); criminal or background history; security
  clearance; signatures and legal attestations (unless Agreements consent); factual education or
  experience claims the history does not support (degrees, dates, years with a skill).
- **One "Screening answers" dial** replaces the low-stakes switch (details in Part 3). Default:
  **Backed by your history**.
- **Where the run lives:** the side panel (approach A), built with three seams so a later move to the
  service worker or an offscreen document is a change of host, not a rewrite.
- **Mark applied:** when a tab shows a confirmation page after you pressed Submit, the run marks
  the job applied, with an undo on its row.
- **Job memory:** the 30-minute, one-key-per-browser `widget.session` memory becomes per-tab memory,
  for queued runs AND for ordinary single-page use.

Rejected: answering in the web app's Agent inbox (more to build; you visit each tab for Submit
anyway); the service worker or an offscreen document as the first host (survives closing the panel,
but costs a checkpoint/resume rewrite or a new permission and relay now); "Queued means I qualify" as
the default; including board-native apply.

## Part 1: Architecture

Three seams, built in from the start:

1. **The runner is host-agnostic.** `shared/queue-run.js` is a plain module with its transport
   injected, as `shared/guided-run.js` already is. The panel is its first host.
2. **Run state is plain data, saved at every page change.** One JSON record per tab:
   `{tabId, jobId, applicationId, proposalId, hosts, step, status, parked}`, mirrored to
   `chrome.storage.session` at every page boundary. Reopening the panel shows where each run got to.
3. **Page operations live in the content scripts.** Classify, find the advance button, sign in and
   fill all run in the page through the existing `sw.js` fan-out and frame gate
   (`inv-frame-earns-data`). Any host can call them.

The runner drives each tab through: open, follow Apply, sign in, fill, advance, ..., review (stop).
Filling is today's `shared/fill-loop.js` with the run's answer rules. Classification and advance are
new content-script operations.

The panel's single `card` store stays the view of the ACTIVE tab. Run records live beside it, pinned
to their own tabs: today's "a tab switch cancels the fill" rule stays for single-page Autofill and
does not apply to run tabs, whose fan-out targets their own `tabId`.

What this does not give for free: runs that survive closing the panel or Chrome (that is the later
move to a service worker or offscreen host, reading the run records), and anything scheduled
(Maestro runs no scheduler; a scheduled run would come from the user's own agent).

## Part 2: Reading the page and clicking Next

**Page kinds.** Code decides first, from DOM signals:

| Kind | Signal | Run does |
|---|---|---|
| Posting | an Apply button or link | follow it; board-native apply parks |
| Sign-in | one visible password input | saved login (Part 4) |
| Create account / email code | confirm-password input, or a one-time-code input | park |
| CAPTCHA | a visible challenge frame (an invisible badge alone does not count) | park |
| Form step | fillable fields and a Next-like button | fill, then Next |
| Final form / review | a Submit-like button and no Next-like one | fill, then stop: Ready to submit |
| Confirmation | "application received / thank you" and no form | mark applied (after your Submit) |
| Closed | "no longer accepting applications" | Position closed |

**Choosing the button.** Code collects visible, enabled buttons near the form and sorts each as
Next-like (next, continue, save and continue, review), Submit-like (submit, apply, send, finish,
complete) or unclear. Only unclear ones go to a new `POST /api/autofill/page`, shaped like `/step`:
the model picks among candidates code labelled Next-like or unclear, and sees button texts only,
never page prose. **A Submit-like button is never clickable. When in doubt, the page is the review
page and the run stops.**

**Before Next:** the fill finished, the resume is attached, the page's receipt is recorded
(`shared/receipt.js`), and no required field is open (an open one parks the page instead).

**After Next:** wait for a change (URL, step heading, or field fingerprint). The same page with
visible error text gets one retry fill on the flagged fields, then parks with the page's own error
text. At most 15 steps per application; the same fingerprint seen twice parks.

**New tabs:** a tab opened by Apply (`openerTabId`) replaces its opener in the run.

**Pins:** a browser test with a set of Submit wordings proves Submit is never clicked.
`content/sections.js`'s `NAV_ID` guard for section Add buttons stays as it is.

## Part 3: Answers

**Screening answers dial** (Settings › Form filling; replaces "Answer low-stakes questions for me"):

| Level | Answered without asking |
|---|---|
| Facts only | saved answers and career history; the rest parks (today's low-stakes Off) |
| Backed by your history (default) | the above; the low-stakes kinds answered in the job's favour; "N years of a skill" Yes when the history shows the skill and does not contradict the years |
| Queued means I qualify | the above; every requirement and years question on a Queued job is Yes |

- The protected list sits outside the dial at every level.
- EEO consent and Agreements (`consent_forms`) stay separate: they are legal permissions, not answer
  style.
- One setting, every reader: the Companion, the queued run, and the user's external agent through
  the Job Search Brief (the "one shared switch" `2026-10-06-application-preferences-design.md` asked
  for).
- Migration: no stored value reads as the new default; an explicit low-stakes `false` reads as Facts
  only; `true` reads as Backed by your history.
- Every answer the dial gives is marked on the receipt and listed under "Check before you submit"
  on the review page.
- The map prompt (`services/autofill_map.py`) gets the level 2 years rule and the level 3 rule; a
  fill request names its job so level 3 can tell a Queued job.

## Part 4: Sign-in with the saved login

- `POST /api/companion/job-site-login`, admitted only for the configured Companion extension
  origin. Same gates as the agent hand-off (`services/proposals.py`): full automation On, job Queued
  or approved, company not skipped. Each hand-off records
  `ConsentEvent(action="login_shared", channel="companion")`, never the value.
- A dedicated `sign_in` content operation types email and password into the TOP frame, only on a
  host the run reached from the job's apply link, then clicks Sign in. The fill loop and the model
  never see a password input; the password never reaches traces, receipts or telemetry.
- No retries: a failed sign-in parks at once ("Sign-in didn't work"), so the account is not locked.
- `inv-job-site-password-local` is rewritten to name the second route.

## Part 5: Running several tabs, the run list, Needs you

- **Start:** an **Apply to queued** button in the panel, shown while full automation is On. It calls
  `GET /api/autofill/queue-run`: Queued jobs minus skipped companies and already-applied, up to the
  remaining daily cap, each with its apply link, PDF readiness and knock-out pre-scan.
- **Before we start:** profile facts the pre-scan found missing are asked once and saved. Jobs with
  a knock-out conflict are listed unticked with "apply anyway"; an unstated requirement just runs.
- **Opening tabs:** a dedicated normal window, 3 tabs filling at once by default (1 to 5 in the
  panel). A job with no PDF is quick-tailored and rendered first, one at a time, while earlier tabs
  fill. Ready-to-submit tabs stay open and do not count as filling; opening pauses at 10 waiting tabs.
- **Run list** at the top of the panel, one row per job: Opening, Filling page N, Needs you (N),
  Ready to submit, Applied, Stopped: reason. A row brings its tab forward and the panel shows that
  tab's Fill view. A parked tab shows today's pause rows, one per open question, "Remember this
  answer" on; answering resumes the tab's run. Stop per row and Stop all.
- **After your Submit:** a confirmation page in that tab marks the job applied with an undo (consent
  channel `frontend`, since you clicked Submit; the confirmation is the submit evidence; the
  proposal goes Queued, approved, submitted, so the daily cap counts it). With no confirmation page
  the row offers Mark applied.
- **Run log:** at the end the Companion posts a Recent runs entry (`agent_runs`, automation
  "Queued run") with counts: ready, needs you, applied, stopped.

**Risk measured first:** Chrome throttles background tabs (no animation frames; timers slow to once
a second, then once a minute after about five minutes hidden). The first build task is a spike:
fill time on a fixture form in a background tab of the run window vs a foreground tab. The default
of 3 follows from it.

## Part 6: Per-tab job memory

`widget.session` is one key per browser with a 30-minute TTL (`panel.js` `SESSION_TTL_MS`), so a
long wizard forgets its job and a second tab's pick overwrites the first. It becomes a per-tab entry
in `chrome.storage.session`, alive while the tab is open and cleared when Chrome closes. The origin
and tenant guards and "the backend wins" guard (`decisions.restorableSession`) stay. The old key is
read once while installs move over. Run tabs bind through their run record, with no timer, across
pages and into a tab Apply opens. The one-hour application-flow window gets the same per-tab
treatment for run tabs.

## Rules and docs changed on purpose

- SYSTEM.md §6: `inv-policy-deny-list-single-source` ("Next/Submit stay the user's" becomes "Submit
  stays the user's at every setting; Next is the Companion's only during a queued run with full
  automation On"), `inv-eeo-standing-consent` (the same sentence), `inv-job-site-password-local`,
  `inv-auto-consent-gated`. §7 Guided fill and Chrome extension.
- `extension/shared/policy.js` header, `extension/README.md`, `extension/INTERNALS.md` ("never
  advances", "Mark applied is never automatic", the session-key notes), `schemas/eeo_consent.py`
  wording, CONTRIBUTING (refusal #1 names the queued run as capped and queued).
- `2026-10-05-full-automation-design.md`: a note pointing here.
- SYSTEM.md is at its line cap: groom before documenting.

## Testing

- Unit fixtures for the classifier, one per page kind, and for button sorting.
- Browser tests (bundled Chromium): a three-step wizard ending on review; Submit wordings never
  clicked; sign-in; CAPTCHA and email-code pages park; a validation-error retry; three tabs at once;
  per-tab memory surviving 30 minutes (clock injected) and a second tab's pick.
- Backend: dial levels and migration, gates on each new route, the proposal transitions on
  auto-mark, the brief carrying the dial.
- Owner live check: a real queued run on Greenhouse, Lever, Workday, Ashby and iCIMS jobs, each
  stopping on its review page.

## Known risks

- Sites that ignore script clicks: the only fix is debugger input, which the owner ruled out; such
  pages park.
- Background-tab throttling (measured first).
- Several tabs mean more concurrent model calls (each `/map`, `/pick`, `/step` has a 9 s budget) and
  more SQLite writes.
