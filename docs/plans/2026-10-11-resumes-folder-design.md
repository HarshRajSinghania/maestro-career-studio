# Resumes folder: a visible copy of every finished resume — design

Status: approved in conversation with the owner on 2026-10-11. Implementation plan:
`2026-10-11-resumes-folder.md`. Where the two differ, the plan wins.

## Goal Card

**Goal:** A user who tailors a resume, whether through the Companion, the web app or an agent, finds the
finished PDF in a normal folder they can see in Finder (or File Explorer) and in every browser upload dialog,
without knowing where Maestro keeps its data. A new user never has to set anything up.

**Why now:** Since the move to the uv install (2026-10-11), application files live in
`~/Library/Application Support/Maestro Career Studio/applications/`. macOS hides `~/Library`, so the
owner's newly tailored resumes looked missing. Under Docker they sat in the repo's `applications/`,
which was visible. Every new uv user, and the future desktop app, will hit the same wall.

**Principles** (how to decide when the plan is ambiguous or wrong):
- **App data stays where it is.** The hidden per-application folder (`inv-stable-artifact-dir`) is
  still the working copy. The app reads, renders, previews, attaches and syncs from it. The design
  touches no stored path, needs no migration and leaves sync bundles unchanged.
- **One-way copies.** Maestro writes copies into the visible folder and never reads them back.
  It never deletes anything there. Replacing a file it wrote earlier (same name) is the only
  overwrite.
- **A copy never breaks the real work.** Any failure to copy is logged and shown in Settings. It
  never fails the render, cover-letter or sync request that triggered it.
- **Works with no setup.** The default is on, with a per-OS Documents location. The user learns
  where files go when the first PDF appears, and can change or turn it off later.
- **Only the machine the user sits at.** Off on the Docker stack (its `applications/` is already a
  visible host folder, and the container cannot see `~/Documents`). Off on the always-on copy
  (nobody browses its folders). The setting never syncs between machines.

**Non-goals:**
- Moving the working folder.
- Letting the user edit the copies and have Maestro pick the edits up.
- Deleting copies when a job is deleted.
- Showing or revealing the copy in the Companion panel.
- Pruning page previews or build leftovers (see Follow-ups).
- A Docker bind mount for Documents.

## What the user gets

1. **On by default.** Every time Maestro makes a tailored resume PDF, it saves a copy here:

   ```
   <Documents>/Maestro Career Studio/Applications/<Company> – <Job title> (<YYYY-MM-DD>)/
       Ajey_Loganathan_DataScientist_Resume.pdf
       Ajey_Loganathan_DataScientist_CoverLetter.pdf   (only once a cover-letter PDF exists)
   ```

   This happens however the PDF was made: Companion quick tailor, **Create PDF** / **Update PDF**,
   the studio's Save or Tailor, MCP `render_pdf`, or a job arriving from the always-on copy with
   its PDF. A re-render replaces the copy. Each folder holds only the PDFs: no `.tex`, logs or
   previews.
2. **Existing jobs filled in once.** After the update, Maestro copies the current PDFs for all
   existing applications (the owner has 160, about 30 MB), in the background at startup.
3. **Told once.** The Resume tab's PDF card shows a dismissible note: "A copy is saved in Documents ›
   Maestro Career Studio › Applications." It has **Show in Finder** and **Change folder** buttons.
   The note reappears in a new browser profile, which is acceptable.
4. **Show in Finder.** The PDF card (Output tab) and the studio's preview header get a button that
   opens the copy's folder with the PDF selected. It is labelled **Show in Finder** on macOS, **Show
   in File Explorer** on Windows and **Show in folder** on Linux.
5. **Settings › Tailoring › Resumes folder.**
   - A switch: "Save a copy of each resume".
   - The current folder, shown as `Documents › Maestro Career Studio › Applications`.
   - **Open folder** and **Change folder** buttons, and **Use the default** when a custom folder is
     set.
   - The last copy error, if any.
   - Changing the folder copies every current PDF into the new one. The old folder is left as it is.
   - Turning the switch off stops new copies and deletes nothing.
   - **Change folder** takes a typed or pasted path. A native folder picker is a gated extra (§6).

## 1. Where the folder is (default, availability)

- **The default is computed by the launcher.** `maestro_cs/home.py` gains
  `documents_dir(platform, environ, user_home, known_folder=...)`, built like `default_home`:
  - macOS: `~/Documents`.
  - Windows: the real Documents folder from `SHGetKnownFolderPath(FOLDERID_Documents)` (ctypes,
    injected for tests), so a OneDrive-redirected Documents is honoured. Falls back to
    `%USERPROFILE%\Documents`.
  - Linux: `XDG_DOCUMENTS_DIR` from `$XDG_CONFIG_HOME/user-dirs.dirs` (`$HOME` expanded). Falls back
    to `~/Documents`.
- **The backend gets it through the environment.** `backend_env` adds
  `RESUMES_FOLDER_DEFAULT=<documents>/Maestro Career Studio/Applications` to the enforced env,
  following `inv-env-before-app`. `app/config.py` gains `resumes_folder_default: Path | None = None`.
- **Availability** (`resumes_folder.availability()`) is computed, not stored:
  - unavailable `"not_here"` when `resumes_folder_default` is None (Docker, a bare dev uvicorn);
  - unavailable `"always_on_copy"` when `sync.status.is_remote()`;
  - otherwise available.
  - The future desktop shell sets the same env var, so it inherits the feature.
  - Dev and browser checks export `RESUMES_FOLDER_DEFAULT` pointing at a scratch folder.

## 2. Settings (machine-local)

- **Keys** live in the `settings` table under a new prefix, `resumes_folder.`:
  - `resumes_folder.enabled`: the row exists only as `"off"`. No row means on.
  - `resumes_folder.path`: a custom root, absolute. No row means the default.
  - `resumes_folder.backfilled`: the root that the last completed backfill filled.
  - `resumes_folder.last_error`: JSON `{code, path, at}`, cleared by the next successful copy.
  - `resumes_folder.app.<application uuid hex>`: the folder name chosen for that application (§3).
- **The prefix is machine-local.** `"resumes_folder."` is added to
  `sync/registry.py LOCAL_SETTING_PREFIXES`, so none of it reaches the other machine. A laptop
  path must never land on the always-on copy.
- **API.** `GET`/`PUT /api/settings/resumes-folder`.
  - `GET` returns `{available, reason, enabled, folder, folder_display, default_folder, custom,
    platform, picker, last_error}`.
    - `platform` is `macos`, `windows` or `linux`, from the backend's `sys.platform`. The frontend
      picks the reveal label from it.
    - `folder_display` replaces the home prefix with `›`-joined segments, e.g.
      `Documents › Maestro Career Studio › Applications`. An outside path shows in full.
    - `last_error` is a plain sentence plus time, never an exception string.
  - `PUT` takes `{enabled?: bool, folder?: str | null}`. Absent leaves a field alone, and `null`
    for `folder` means "use the default".
    - It refuses with 409 when unavailable.
    - A custom folder must be absolute, exist, be a directory, be writable (mkstemp probe), and not
      sit inside `MAESTRO_HOME` or `applications_dir`. Each refusal is a 422 with a plain sentence.
    - A changed root (or re-enabling) starts a background backfill.

## 3. The copy engine (`app/services/resumes_folder.py`)

- **`copy_application(application_id) -> Path | None`** opens its own `SessionLocal` session, so it
  never touches the caller's transaction.
  - It returns None at once when unavailable or off.
  - It resolves the application's folder name, then copies the resume PDF (`application.pdf_path`,
    if the file exists) and every cover-letter PDF (`QAEntry.kind == "cover_letter"` with an
    existing `pdf_path`). Each file keeps its working name.
  - It returns the resume copy's path, or None when there is no resume PDF.
  - It catches `OSError` (and `sqlalchemy.exc.SQLAlchemyError` from its own session), logs a
    warning with traceback and records `last_error`. It never raises.
- **`copy_job(job_id)`** runs `copy_application` for each of the job's applications.
  **`copy_all()`** does the same for every application with a PDF; it is the backfill.
- **Folder name:** `"{company} – {title} ({YYYY-MM-DD})"`.
  - Fields: `Job.company`, `Job.title` (the posting's own title, which the user recognises) and the
    application's `created_at` as a local date.
  - Sanitised for every OS:
    - drop `/ \ : * ? " < > |` and control characters;
    - collapse whitespace;
    - strip trailing dots and spaces;
    - prefix `_` to Windows reserved names (`CON`, `NUL`, `COM1`…);
    - cap each part so the whole name stays at or under 100 characters.
  - Empty company or title becomes `Untitled`.
- **The name is stable.** It is chosen on the first copy and saved in
  `resumes_folder.app.<id>`, so a later title edit does not scatter copies across folders.
  - On a clash with another application's saved name, it appends ` (2)`, ` (3)` and so on.
  - Two first copies of the same application racing on the insert hit the primary key. The loser
    rolls back its own session and re-reads.
- **The write is atomic, with no churn.**
  - It first compares the source and target `stat` (same size, `mtime` within 2 s); a match skips
    the write.
  - Otherwise it writes `mkstemp` in the target folder, then `copyfile`, `copystat`, `os.replace`,
    and unlinks the temp file in `finally`.
  - It never opens the target for reading. In a Documents folder synced by iCloud or OneDrive, the
    file may be a dataless placeholder, and reading it would trigger a download.
- **Missing folders.**
  - The default root (and its `Maestro Career Studio/Applications` parents) is created on demand.
  - A custom root that has vanished (an unplugged drive, a deleted folder) is not recreated. That
    records `last_error` `"missing"`, so nothing is written to an empty mount point.

## 4. Hook points (after the commit that made the PDF real)

1. **`application_render.render_resume`**, right after `db.commit()`. It is the one funnel for
   every application resume PDF: web render, studio Save and Tailor, quick tailor and the
   Companion, and MCP `render_pdf` via HTTP. Its return value is unchanged. The router fills a new
   `RenderResult.copy_path: str | None` from `resumes_folder.copy_path_for(application_id)`, a
   cheap name lookup that does no I/O beyond one `exists()`. MCP `render_pdf` relays the response,
   so an agent can tell the user where the copy is.
2. **`routers/qa.render_cover_letter`**, after its commit.
3. **`routers/sync._store` and `_take_back`**, after a bundle is applied successfully. These are
   the laptop-side arrivals that bring a PDF rendered on the always-on copy. On the always-on copy
   itself, the copy is unavailable, so the call is a no-op.
4. **Startup backfill.** The lifespan starts a daemon thread that runs `copy_all()` when the
   feature is available and on, and `resumes_folder.backfilled` differs from the current root. On
   completion it stores the root. The thread is started after `seeding.run_startup()`; a failure
   only logs. A `PUT` that changes the root or re-enables the feature starts the same thread.

The same `copy_application` call runs before every reveal (§5), so a copy missed for any reason
heals the moment the user asks for it.

## 5. Reveal (open the folder for the user)

- **`app/services/folder_opener.py`** builds the argv per platform and starts it with `Popen`.
  - macOS: `["open", "-R", file]` for a file, `["open", folder]` for a folder.
  - Windows: `["explorer", f"/select,{file}"]` and `["explorer", folder]`.
  - Linux: `["xdg-open", folder]`, opening the containing folder for a file, since `xdg-open`
    cannot select.
  - `Popen` runs with no shell, stdin/stdout/stderr to DEVNULL, `start_new_session` on POSIX and
    `close_fds`. Nothing waits on it.
  - A missing program raises `OpenerUnavailable`.
- **Endpoints:**
  - `POST /api/applications/{id}/show-copy` runs `copy_application`, then reveals the resume copy.
    It returns `{path}`, or 409 with a plain sentence when unavailable, when there is no PDF, or
    when the copy failed.
  - `POST /api/settings/resumes-folder/open` opens the root, creating the default root first if
    needed.
- **Safety.** Neither endpoint accepts a path. They open only paths Maestro computed itself. The
  existing Host allowlist and `OriginGuardMiddleware` apply. Both refuse with 409 when unavailable,
  which includes Docker, where a container cannot open the host's Finder.

## 6. Native folder picker (gated)

- **The endpoint:** `POST /api/settings/resumes-folder/choose` shows the OS folder dialog and
  returns `{folder: str | null}` (null on cancel). It is guarded by a lock so only one dialog can be
  open, with a 5-minute timeout.
  - macOS: `osascript` `choose folder`.
  - Windows: PowerShell `System.Windows.Forms.FolderBrowserDialog` (`-STA`).
  - Linux: `zenity --file-selection --directory` when it is on `PATH`.
  - `GET` reports `picker: false` where none exists, and the UI then offers only the path field.
- **Gate (plan Task 0 spike):** under `maestro run` (and the macOS launchd autostart), the dialog
  must appear in front of the browser without asking for any Automation or Accessibility
  permission.
  - If macOS fails the gate, the picker task is dropped. The typed path stays the only way to
    change the folder until the Electron shell, which has `dialog.showOpenDialog` for free.
  - The rest of the design does not depend on the picker.

## 7. Frontend

- **Settings › Tailoring:** a new `ResumesFolderSection` (`id="resumes-folder"`, added to the
  Tailoring tab's `anchors`), after Quick tailor. It follows `mcp-workflow-section.tsx`:
  `SettingCard` + `SwitchRow` + `AutosaveStatus`.
  - The folder row shows `folder_display`, **Open folder**, **Change folder** (an inline path field
    with **Save** and **Cancel**, plus **Choose…** when `picker`) and **Use the default** when
    `custom`.
  - An unavailable install shows one sentence and no controls.
    - Docker: "Your resumes are already in the applications folder of your Maestro checkout."
    - The always-on copy: "This copy keeps no resumes folder."
- **The Output tab PDF card** (`application-panel.tsx` `OutputTab`):
  - an outline **Show in Finder** button in the button row, shown when available, enabled and the
    PDF exists;
  - the one-time note below the row (dismissed with `useLocalStorageState` key
    `maestro-cs:resumes-folder-note-dismissed`).
- **The studio** (`tailored-resume-studio.tsx`) gets a ghost `icon-sm` reveal button beside
  Download and Open.
- **Shared code:** both read `GET /api/settings/resumes-folder` through one query key,
  `["settings", "resumes-folder"]`. The label comes from one helper, `revealLabel(platform)`.
- **Copy rules** (`docs/frontend-conventions.md`): sentence case, no semicolons, no "e.g.", no em
  dash as a joiner, and every sentence at 25 words or fewer. Errors go through `couldnt(...)`:
  "Couldn't open the folder." or "Couldn't save a copy to that folder." "Finder" and "File
  Explorer" enter the glossary as OS names shown only through `revealLabel`.

## 8. Errors and edge cases

- **Disk full, permission denied or a read-only folder.** The render still succeeds. A warning is
  logged and `last_error` is recorded. The Settings card shows "Maestro couldn't save the last copy
  to this folder. Check that it still exists and that you can write to it."
- **A user edits or replaces a copy.** The next re-render of that application overwrites the file
  with the same name. This is documented in USING_MAESTRO, and Maestro never merges edits.
- **A user renames or moves an application's folder.** The next copy recreates the stored name.
  This is documented.
- **The job or application is deleted in Maestro.** Its copies stay, since the visible folder is
  the user's archive. The `resumes_folder.app.<id>` row is left behind as harmless (a few bytes).
- **The working file name changes** (a new contact name or role label). The new file sits beside
  the old copy. The old copy is not deleted.
- **iCloud or OneDrive.** Copies may be evicted. The app never reads them, so nothing breaks.

## 9. Testing

Backend, with new files `tests/test_resumes_folder.py`, `tests/test_folder_opener.py` and
`tests/maestro_cs/test_home.py` additions:

- **Folder location and availability:**
  - `documents_dir` on all three platforms, including `user-dirs.dirs` parsing and the Windows
    known-folder fallback;
  - `backend_env` sets `RESUMES_FOLDER_DEFAULT`;
  - availability: no default, remote and available.
- **Folder naming:** sanitising (reserved names, illegal characters, length), the stable saved name
  and the clash suffix.
- **Copying:**
  - copy idempotence (a second call leaves `mtime_ns` unchanged);
  - the atomic write leaves no temp file;
  - the copy never reads the target (a monkeypatched `open` fails on the target path);
  - failure handling: `copyfile` raises, the render endpoint still returns 200 and `last_error`
    is set;
  - a vanished custom root is not recreated.
- **Hooks:** the render endpoint writes the copy and fills `copy_path`, the cover-letter render
  copies, and the sync `_store` / `_take_back` path copies.
- **Backfill:** it runs once per root and runs again after a root change.
- **Machine-local settings:** `is_profile_row` is False for `resumes_folder.*`.
- **Settings PUT:** every validation refusal; 409 when unavailable.
- **Reveal:** argv per platform with `Popen` monkeypatched; `show-copy` and `open` return 409 when
  unavailable.

Frontend: `tsc`, `lint` and `build`, plus the vocabulary, sentence-length and error-words ratchets
in `backend/tests`. `settings-tabs.test.ts` covers the new anchor.

Browser check on a scratch stack (`MAESTRO_INSTALL=uv`, a scratch `MAESTRO_HOME` and
`RESUMES_FOLDER_DEFAULT`):
- render a PDF and see the note;
- **Show in Finder** opens the folder;
- change the folder and the backfill fills it;
- turn it off and no new copy appears.

The live app is never used for the check.

## 10. Docs touched

- `SYSTEM.md`: §5 step 7, the new §6 invariant `{#inv-resumes-folder-one-way}` (pinned in
  `.system_md_enforcement.json` to `tests/test_resumes_folder.py`), and §7 `render_pdf`
  `copy_path`.
- `docs/entities/others.md` (settings keys).
- `README.md` "Where your files live" (one sentence and the per-OS default).
- `docs/USING_MAESTRO.md` (the resumes folder and its rules).
- `docs/SETUP_REFERENCE.md` (`RESUMES_FOLDER_DEFAULT`; Docker unchanged).
- `KNOWN_ISSUES.md` (not on Docker or the always-on copy, and the native picker state).
- `CHANGELOG.md` `[Unreleased] › Added`, whose opener is in the owner's voice.

## Follow-ups (not in this design)

- **Tidy closed jobs** (the owner's idea, 2026-10-11): when an application is closed (rejected,
  withdrawn or archived), delete its page previews (`.pages/`, 83 MB of the owner's 115 MB) and
  build leftovers (`.log/.aux/.out`) and keep the PDF and source. Previews regenerate on view.
- **Companion panel:** a "Saved to Documents › …" line after quick tailor, with a reveal action
  through the same endpoint.
- **Electron (branch C):** replace the picker endpoint with `dialog.showOpenDialog`.
- **Found in passing:**
  - `routers/qa.py` PATCH/DELETE and `services/qa.py` regenerate unlink cover-letter PDFs
    before the commit, which breaks the stage-then-remove rule.
  - The Typst path writes the PDF in place, not atomically (`pdf_render.py:563`,
    `typst_compiler.py:243`).
