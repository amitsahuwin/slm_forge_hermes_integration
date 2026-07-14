# use-cmux skill

A Claude Code skill that makes Claude route parallel/multi-agent work into
visible split panes in your current [cmux](https://cmux.com) workspace, so you
can watch every agent, sub-agent, task, build, and long-running command live —
plus surface progress in the cmux sidebar.

## Install (Claude Code)

Copy the `use-cmux` folder into your Claude Code skills directory:

```bash
# user-level (applies everywhere)
mkdir -p ~/.claude/skills
cp -r use-cmux ~/.claude/skills/

# or project-level (this repo only)
mkdir -p .claude/skills
cp -r use-cmux .claude/skills/
```

Claude Code auto-discovers `~/.claude/skills/<name>/SKILL.md` and loads it when
the task matches the skill's description.

## Requirements

- macOS with the cmux app installed and running (`brew install --cask cmux`).
- Claude Code launched **inside** a cmux terminal (so the cmux socket is
  reachable). The skill detects cmux and does nothing if it's absent.

## What's inside

- `SKILL.md` — the behavior Claude follows (detect cmux → one pane per parallel
  task → sidebar status/progress → notify → clean up).
- `scripts/cmux-panes.sh` — helper that launches one command per split pane.

## Requested design

Applies to Claude Code (CLI); parallel work runs as split **panes in one
workspace** (not separate workspaces).

Based on the official cmux CLI reference: https://cmux.com/docs/api