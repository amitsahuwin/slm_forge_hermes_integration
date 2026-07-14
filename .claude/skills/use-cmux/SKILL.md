---
name: use-cmux
description: >-
  Run work in visible cmux panes so the user can watch every agent, sub-agent,
  task, and activity live. ALWAYS load and follow this skill at the start of ANY
  task when running inside cmux — especially before spawning sub-agents, running
  parallel/background work, long builds/tests, or multi-step tasks. Route each
  parallel unit of work into its own split pane in the current cmux workspace,
  and surface progress via the sidebar. Triggers: cmux, "show me everything",
  "see all agents", "watch the agents", parallel agents, subagents, background
  tasks, orchestration.
---

# Use cmux — make every agent and task visible

The user wants full visibility into what you are doing. Whenever you run inside
[cmux](https://cmux.com), do work in **split panes of the current workspace** so
every agent, sub-agent, task, build, and long-running command shows up as its
own live pane — never as a hidden background process. Surface state through the
cmux sidebar (status pills, progress bar, log) so the user can glance and know
what is happening.

This skill applies to Claude Code running inside a cmux terminal. It uses the
real `cmux` CLI documented at https://cmux.com/docs/api. Do not invent flags.

## Step 0 — Detect cmux (do this first, every task)

Only drive cmux when it is actually present. Run:

```bash
# CLI present?
command -v cmux >/dev/null 2>&1 || { echo "cmux CLI not found"; }
# Socket reachable? (default release socket)
SOCK="${CMUX_SOCKET_PATH:-/tmp/cmux.sock}"
[ -S "$SOCK" ] && cmux ping
# Are we inside a cmux-managed surface?
[ -n "${CMUX_WORKSPACE_ID:-}" ] && [ -n "${CMUX_SURFACE_ID:-}" ] && echo "inside cmux"
```

If `cmux ping` returns a pong and `CMUX_WORKSPACE_ID` is set, you are inside
cmux — follow this skill. **If cmux is not detected, do the work normally and do
not mention cmux.** Never fail a task just because cmux is missing.

Note on access: by default cmux only lets processes spawned inside its own
terminals connect to the socket. Since Claude Code is running inside cmux, this
works out of the box.

## The rule: one pane per parallel unit of work

Whenever you are about to run more than one thing at once, or kick off anything
long-running or backgrounded, give it a pane instead of hiding it:

- Spawning sub-agents / a team of agents → one pane per agent.
- Running tests + a dev server + a watcher → one pane each.
- A long build, migration, or install → its own pane so its output stays visible.
- Anything you would otherwise run with `&` or in the background → a pane.

Keep them all in the **current workspace** (the user asked for panes in one
workspace, not separate workspaces). Prefer readable layouts: split `right` for
the second pane, then `down` to stack additional ones.

## Step 1 — Create a pane and capture its surface id

```bash
# Split the current pane. Directions: left | right | up | down
cmux new-split right

# Find the surface id of the pane you just created
cmux list-pane-surfaces --json   # surfaces in the focused pane
cmux list-panels --json          # all surfaces in the workspace
```

Capture the new pane's `surface` id from the JSON so you can target it directly
instead of relying on which pane happens to be focused. Focus a specific pane
later with:

```bash
cmux focus-panel --panel <surface-id>
```

## Step 2 — Launch the agent / task in that pane

Send the command to the target surface, then press enter. Always target
`--surface <id>` so output lands in the right pane:

```bash
cmux send --surface <id> "claude -p 'implement the auth refactor'"
cmux send-key --surface <id> enter
```

For sub-agents, launch one Claude Code (or other agent) invocation per pane the
same way. Give each pane a clear job. Example loop for N tasks:

```bash
# tasks is an array of shell commands, one per sub-agent/task
for i in "${!tasks[@]}"; do
  if [ "$i" -eq 0 ]; then
    dir=right
  else
    dir=down
  fi
  cmux new-split "$dir"
  sid=$(cmux list-pane-surfaces --json | jq -r '.surfaces[-1].surface')
  cmux send --surface "$sid" "${tasks[$i]}"
  cmux send-key --surface "$sid" enter
done
```

(If `jq` is unavailable, parse the id however is convenient — the exact JSON key
is whatever `cmux list-pane-surfaces --json` returns; inspect it once.)

## Step 3 — Surface progress in the sidebar

So the user can see status at a glance without reading every pane, update the
sidebar for the workspace. Use a unique status key per concern:

```bash
cmux set-status agents "3 running" --icon hammer --color "#2563eb" --priority 80
cmux set-progress 0.33 --label "1/3 tasks done"
cmux log --level info  --source orchestrator "Spawned 3 sub-agents"
cmux log --level success --source tests -- "All tests passed"
```

As work completes, advance the progress bar and clear finished status pills:

```bash
cmux set-progress 1.0 --label "Done"
cmux clear-status agents
cmux clear-progress
```

Status log levels: `info`, `progress`, `success`, `warning`, `error`.

## Step 4 — Notify when work needs attention or finishes

cmux shows notification rings on panes automatically, but fire an explicit
notification when a task finishes or is blocked waiting on the user:

```bash
cmux notify --title "✓ Task complete" --body "Auth refactor merged, tests green"
cmux notify --title "Needs input" --body "Migration is waiting for confirmation"
```

## Step 5 — Clean up

When the run is over, leave the workspace tidy: clear sidebar state you set
(`cmux clear-status <key>`, `cmux clear-progress`, `cmux clear-log`). Leave the
panes so the user can review output; only close them if the user asks.

## Quick command reference

| Purpose | Command |
| --- | --- |
| Health check | `cmux ping` |
| Where am I | `cmux identify --json` |
| New pane | `cmux new-split right\|down\|left\|up` |
| List panes in focused pane | `cmux list-pane-surfaces --json` |
| List all workspace surfaces | `cmux list-panels --json` |
| Focus a pane | `cmux focus-panel --panel <id>` |
| Run command in a pane | `cmux send --surface <id> "<cmd>"` then `cmux send-key --surface <id> enter` |
| Sidebar status pill | `cmux set-status <key> "<text>" --icon <name> --color <hex> --priority <n>` |
| Progress bar (0.0–1.0) | `cmux set-progress <f> --label "<text>"` |
| Sidebar log | `cmux log --level <lvl> --source <src> -- "<msg>"` |
| Notification | `cmux notify --title "<t>" --body "<b>"` |
| Clear | `cmux clear-status <key>` / `cmux clear-progress` / `cmux clear-log` |

## Notes and boundaries

- cmux is macOS-only and the CLI/socket exist only when the cmux app is running.
  Everything here is a no-op fallback when cmux is absent — degrade gracefully.
- Full CLI reference: https://cmux.com/docs/api. cmux also ships official skills
  (`npx skills add manaflow-ai/cmux`) covering deeper workspace/pane control,
  settings, and the built-in browser — install those if you want more than this
  skill's opinionated "always show the work" behavior.
- Don't over-split: if a task is genuinely a single sequential step, one pane is
  fine. The goal is visibility of *parallel* work, not clutter.