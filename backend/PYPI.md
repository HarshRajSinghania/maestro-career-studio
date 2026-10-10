# Maestro Career Studio

A local job-application copilot: capture job descriptions, score resumes with a deterministic
ATS engine, tailor and render a PDF, track every application. It runs on your machine and the
data stays in one folder you own. A Chrome Companion and an MCP server connect your browser
and your AI agent to the same app.

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

## Run

`maestro start`, then open http://localhost:3000. `maestro stop` closes it, `maestro update`
updates it (your data is snapshotted first), and `maestro connect` connects an AI agent.

## Where your data lives

- macOS: `~/Library/Application Support/Maestro Career Studio`
- Windows: `%APPDATA%\Maestro Career Studio`
- Linux: `$XDG_DATA_HOME/maestro-career-studio` (default `~/.local/share/maestro-career-studio`)

## More

- Source and docs: https://github.com/seinun-ai/maestro-career-studio
- What leaves your machine, and when: https://github.com/seinun-ai/maestro-career-studio/blob/main/PRIVACY.md
