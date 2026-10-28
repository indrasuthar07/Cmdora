# Cmdora

Describe what you want to do in plain English. Cmdora figures out the right command, runs it, and shows you the result.

```powershell
cmdora "list all python files"
cmdora "git status"
cmdora "commit my changes with message fix navbar bug"
```

No memorizing flags. No copy-pasting from Stack Overflow. Just tell it what you need.

---

## Install

```powershell
pip install cmdora
```

Then set your Gemini API key once:

```powershell
cmdora config set-key
```

You'll be prompted to paste your key. It gets saved to `%APPDATA%\CmdoraAI\.env` — you only do this once, and it works from any folder after that.

---

## How it works

Cmdora takes your plain-English request, builds a safe execution plan using Gemini, validates it against a strict schema, checks it against a local policy (allowlist, path constraints, no secrets, no destructive ops), and only then runs it. Every action is logged to a local SQLite file — nothing is sent anywhere except what's needed for planning.

```
your request
    → deterministic workflow? → run it
    → or: ask Gemini for a structured plan
        → validate schema
        → check local policy
        → approval prompt (or auto if in autonomy mode)
        → bounded PowerShell executor
        → audit log
```

---

## Common commands

```powershell
# file operations
cmdora "list all files modified today"
cmdora "create src/components/Button.js"

# git
cmdora "git status"
cmdora "push my current branch"
cmdora "what does git rebase do?"

# inspect before running
cmdora "commit my changes" --dry-run

# see history
cmdora history --last 10
```

`--dry-run` shows you exactly what would run without executing anything. Good habit before anything that writes.

---

## Safety

Cmdora was built with a specific constraint in mind: an LLM should help you work, not become an unrestricted shell. So it:

- never runs arbitrary commands — everything goes through an allowlist
- blocks deletion, force-push, encoded PowerShell, secret file access, remote downloads, and package installs by default
- keeps Gemini's context minimal — it only sees your request, repo name, git state, and a list of supported actions; never file contents, `.env` values, or tokens
- logs everything locally at `%APPDATA%\CmdoraAI\history.db`, with secrets redacted

Autonomy mode exists if you want Cmdora to skip approval prompts for safe operations:

```powershell
cmdora "create src/utils/date.py" --autonomy git
```

Even in autonomy mode, deletions, force-pushes, and protected-branch operations are still blocked.

---

## Requirements

- Windows (PowerShell)
- Python 3.12+
- Git
- A [Gemini API key](https://aistudio.google.com/app/apikey) (free tier works)

---

## Contributing

Fork the repo, create a branch off `main`, and open a PR. Keep changes focused. Run the tests before pushing:

```powershell
uv run pytest -q
uv run ruff check src tests scripts
uv run mypy
```

Don't commit `.env`, API keys, or `history.db`.

---

## License

MIT
