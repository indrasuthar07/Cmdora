# Cmdora

What if you could use your terminal without having to remember every command, flag, or combination of arguments?

What if, instead of searching for the right syntax, you could simply describe what you want to accomplish?

That's where Cmdora comes in.

Cmdora is an AI-powered command-line assistant that transforms natural language into executable terminal commands. Describe your goal in plain English, let AI figure out the steps, review the proposed actions, and execute them on your machine with safety policies and user control built into the process.

From everyday Git operations to repetitive development workflows, Cmdora turns what you want to do into commands that get it done.
No need to memorize the exact syntax. Just describe the task.

### See It in Action

` cmdora "find which files I changed today"`

` cmdora "start the development server"`

` cmdora "list all files modified in the last 24 hours"`

` cmdora "list all running Docker containers"`

## User configuration

Run `Cmdora config init` to create the default user configuration. On Windows it is stored at
`%APPDATA%\CmdoraAI\config.toml`, outside the repository. The file contains model, timeout,
output-size, autonomy-mode, and protected-branch settings; API credentials are not stored there.

## Project configuration

Run `Cmdora config init --project` from a workspace to create `.Cmdora/config.toml` there.
Project settings stay inside that workspace and include an autonomy mode, allowed write
directories (`src` and `tests` by default), and protected Git branches. File creation is
restricted to the configured directories; without a project config, writes remain bounded to
the current workspace.

## Audit history

Cmdora records deterministic CLI actions in SQLite at `%APPDATA%\CmdoraAI\history.db` on
Windows. `Cmdora history` shows the latest records, including request ID, timestamp, user
request, workspace, LLM plan (when available), policy and approval decisions, command result,
duration, and redacted output.
