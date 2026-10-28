# Cmdora

Built this because I kept forgetting exact PowerShell and Git flags, wasting time on syntax I already knew conceptually. Cmdora lets you just say what you mean - it figures out the command, runs it, and shows you the result.

```powershell
cmdora "list all python files in src"
cmdora "show the files modified today"
cmdora "commit my changes with message refactor auth module"
```

That's the whole idea. No mode-switching, no config files to learn, no prompt engineering. Just describe the task.

**→ [cmdora-ai.vercel.app](https://cmdora-ai.vercel.app)** - see a walkthrough, and install guide if you want to check it out before installing.

---

## Install

```powershell
pip install cmdora
```

Then add your Gemini API key (free tier is enough):

```powershell
cmdora config set-key
```

It'll prompt you to paste the key. Saves it once to `%APPDATA%\CmdoraAI\.env` and that's it -works from any folder from that point on. You can get a free key at [aistudio.google.com](https://aistudio.google.com/app/apikey) if you don't have one yet.

---

## What it actually does

When you type a request, Cmdora first checks if it matches a built-in deterministic workflow (things like `git status`, listing files, creating a file). If it does, it runs that directly - no API call needed.

If it doesn't match a built-in, it asks Gemini to produce a structured execution plan. That plan gets validated against a strict schema, checked against a local policy layer, and then either runs with your approval or silently if you've enabled autonomy mode.

Nothing fancy goes to Gemini. It only sees your request text, the repo name, current git state, and a list of supported action types. It never sees your file contents, `.env` values, tokens, or actual diffs. Keeping that context narrow is intentional.

Every run gets logged locally to `%APPDATA%\CmdoraAI\history.db` with secrets stripped out.

---

## Things you can do with it

```powershell
# find stuff
cmdora "list all python files"
cmdora "list all files modified today"

# git, without remembering the flags
cmdora "git status"
cmdora "push my current branch"
cmdora "commit my changes with message fix navbar bug"

# ask questions
cmdora "what does git rebase do?"

# create files
cmdora "create src/components/Button.js"

# see the plan before committing to it
cmdora "commit my changes" --dry-run

# review what ran
cmdora history --last 10
```

`--dry-run` is worth using the first few times. It prints exactly what would execute without actually running anything.

---

## On safety

The thing that made this tricky to build was keeping the LLM useful without letting it become a general-purpose shell that can do anything it wants. Cmdora handles this with a few hard rules:

- Everything goes through an allowlist. Arbitrary commands don't run.
- Deletion, force-push, encoded PowerShell, `.env` access, remote downloads, and package installs are blocked at the policy layer - not negotiable, not configurable.
- Gemini never sees sensitive context. It gets the minimum needed to make a plan.
- Every action is logged locally with secrets redacted.

If you want Cmdora to skip the approval prompt for safe operations, you can turn on autonomy mode per-repo:

```powershell
cmdora "create src/utils/date.py" --autonomy git
```

Even then, anything destructive or involving protected branches stays blocked.

---

## Requirements

- Windows with PowerShell
- Python 3.12 or later
- Git
- A Gemini API key - [get one free here](https://aistudio.google.com/app/apikey)

---

## Contributing

If something's broken or you want to add a workflow, fork the repo and open a PR against `main`. Keep the change tight and add tests for anything behavioral. Run these before you push:

```powershell
uv run pytest -q
uv run ruff check src tests scripts
uv run mypy
```

Don't commit `.env` files, API keys, or `history.db`. The `.gitignore` covers most of this already.

---

## License

MIT
