# Maestro Career Studio

If your resume folder has a `v2finalcopy(3)` in it, this is for you.

Maestro keeps one record of what you have actually done and builds each tailored resume from it.
Paste a job description, see how well a resume matches (fixed rules, so the same resume and job
always get the same score), tailor it, and render a real PDF. Every application stays tracked
in one place. A Chrome Companion and an MCP server let your browser and your AI agent work from
that same record.

It all runs on your machine, and your data lives in one folder you own. Here, control is the
whole point.

## Install

macOS and Linux (between the two commands, open a new terminal or run `source $HOME/.local/bin/env`):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install --python 3.12 maestro-career-studio
```

Windows (open a new PowerShell window between the two commands):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv tool install --python 3.12 maestro-career-studio
```

If `maestro` is not found afterwards, run `uv tool update-shell` and open a new terminal.

Best effort: on Windows ARM64 the PDF engine (typst) has no ARM64 wheel, so install an x64 Python under
emulation: `uv tool install --python cpython-3.12-windows-x86_64-none maestro-career-studio`. Alpine and
other musl Linux are not supported by the PDF engine's wheels; use a glibc distro or Docker.

## Run

`maestro start`, then open http://localhost:3000. `maestro stop` closes it, `maestro update`
updates it (your data is snapshotted first), and `maestro connect` connects an AI agent.
Coming from the Docker install? Stop it, then `maestro import-docker <your checkout>` copies your
data over once (the Docker install is only read).

## Where your data lives

- macOS: `~/Library/Application Support/Maestro Career Studio`
- Windows: `%APPDATA%\Maestro Career Studio`
- Linux: `$XDG_DATA_HOME/maestro-career-studio` (default `~/.local/share/maestro-career-studio`)

## More

- Source and docs: https://github.com/seinun-ai/maestro-career-studio
- What leaves your machine, and when: https://github.com/seinun-ai/maestro-career-studio/blob/main/PRIVACY.md
