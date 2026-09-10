---
name: omarchy-hermes-deck
description: Operate, audit, troubleshoot, or extend the Hermes Deck Quattro plugin for Omarchy. Use for the io.github.mbot11.hermes-deck service/bar-widget pair, its deck-collect snapshot contract, deck-act action allowlist, the TUI modal, or marketplace validation. Do not use this skill to install Hermes itself, modify privileged bridges, or touch credential stores.
version: 0.2.0
author: mbot11
license: MIT
---

# Hermes Deck skill

Bounded instructions for operating and maintaining the plugin.

## Constants

```text
PLUGIN_ID=io.github.mbot11.hermes-deck
PLUGIN_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/io.github.mbot11.hermes-deck
REPOSITORY=https://github.com/mbot11/omarchy-hermes-deck
STATE_DIR=${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/hermes-deck
HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
```

## Validate

```bash
omarchy plugin validate "$PLUGIN_DIR"
bash -n "$PLUGIN_DIR/scripts/deck-act"
python3 tests/test_collect.py        # from the repository checkout
"$PLUGIN_DIR/scripts/deck-collect" | jq .
```

## Snapshot contract (schemaVersion 2)

`deck-collect` emits one JSON object: `installed`, `isDefaultAgent`,
`desktopAvailable`, `version`, `model`, `modelSource` (`config`|`cli`|`unknown`),
`gateway` `{serviceState, enabled, connectedPlatforms[], activeAgentsCount}`,
`paused`/`reason`/`engagedAt` (ESTOP sentinel), `activity`
`{working, lastMessageAt, secondsSinceLastMessage}`, `usage`
`{today, week, month, byModelToday}`, `sessions[]`, `auth[]`,
`kanban` `{available, boards[]}`, `errors[]`.

Rules that must not regress:

- Fast path (no flags) never spawns a `hermes` process.
- A dict-format `model:` block (custom endpoint) returns empty and defers
  to `hermes config get model.default` — never guess (hermes-harness#2).
- Missing database/config degrades sections; `errors[]` notes why.
- Credential stores (auth.json, .env) are never read.

## Actions

`deck-act pause|resume|gateway-restart|launch-desktop|set-default-agent|new-session [prompt]|resume-session <id>|set-model <model>`.
Destructive actions are two-click confirmed in the panel. Add new actions
by extending the allowlist in `deck-act` and the `runAction` call sites —
never by passing shell strings.

## Cadence

Fast collect every 5 s (pure local, ~0.25 s). Slow refresh every 60 s;
TTLs inside `deck-collect` gate the actual `hermes` CLI calls (version
30 min, auth 10 min, model fallback 10 min, kanban 60 s). If you change
TTLs, re-run `tests/test_collect.py`.

## Live exercise

```bash
omarchy plugin validate "$PLUGIN_DIR"
omarchy restart shell
omarchy-shell shell ping
omarchy-shell shell summon io.github.mbot11.hermes-deck '{}'
omarchy-shell io.github.mbot11.hermes-deck.modal status
```

Inspect the shell log if a component fails to load. Never claim gateway,
session, or usage state without reading a fresh snapshot.
