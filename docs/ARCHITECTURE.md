# Architecture

## Purpose

Hermes Deck is a live companion surface for a user-local Hermes Agent on
Omarchy: one persistent state daemon feeding a bar glyph and a panel with
Status, Actions, Sessions, Usage, and Kanban sections, plus a drop-down TUI
modal. It complements Hermes; it never replaces it.

## Component map

```text
Omarchy Quattro shell
├── Service.qml  (kind: service, keepLoaded)
│   ├── collector cadence: fast snapshot (5 s), slow refresh (60 s, TTL-gated)
│   ├── action dispatch: one scripts/deck-act invocation at a time
│   ├── notification transitions → omarchy-notification-send
│   └── Modal.qml: drop-down TUI on a Hyprland special workspace
└── BarWidget.qml  (kind: bar-widget)
    ├── state-colored glyph + pulse while working
    ├── shell.serviceFor(pluginId) discovery (retry timer)
    └── Panel.qml
        ├── Status, Actions, Sessions, Usage, Kanban sections
        └── two-step confirmation for destructive actions

scripts/deck-collect (python3 stdlib only)
├── fast path, every poll, no Hermes process:
│   ├── state.db read-only (sqlite3 mode=ro): activity, usage, sessions
│   ├── config.yaml top-level `model` micro-parse (scalar / default: / dict-refusal)
│   ├── ESTOP sentinel read ($HERMES_HOME/ESTOP)
│   └── systemctl --user is-active / is-enabled hermes-gateway.service
└── slow path, TTL-cached in $XDG_STATE_HOME/omarchy/hermes-deck/cache.json:
    ├── hermes --version        (TTL 30 min)
    ├── hermes auth list        (TTL 10 min)
    ├── hermes config get model.default   (TTL 10 min, only if YAML parse empty)
    └── hermes kanban boards list --json  (TTL 60 s, only with --include-kanban)

scripts/deck-act: the only programmatic, non-interactive path that
changes Hermes state (the TUI modal launches a full Hermes session,
which is Hermes by definition)
└── fixed-argument execs: pause, resume, gateway-restart,
    new-session [prompt], resume-session <id>, set-model <model>
## Why this shape

- **Push-shaped, poll-cheap.** The 5 s fast path spawns one Python process
  (~0.25 s) and never a Hermes CLI (a cold Hermes CLI costs seconds). Slow
  fields refresh on TTLs, so CLI cost amortizes to minutes.
- **Service kind.** State lives in one persistent QML item shared by the bar
  widget and panel through `shell.serviceFor(pluginId)`, the same pattern
  the first-party shell and Snitch use.
- **Hermes' own money numbers.** Token totals and estimated costs come from
  Hermes' `session_model_usage` (including its `estimated_cost_usd`), not
  from third-party billing APIs. No provider keys are ever read.
- **Fail-muted everywhere.** Missing database, missing CLI, malformed
  config: the section degrades and `errors[]` notes it. Unknown state is
  displayed as unknown, never invented.

## Data-source evolution

The fast path is the universal baseline (works on every Omarchy machine
running Hermes). A later phase can add `hermes serve` WebSocket push
(127.0.0.1:9119) behind the same Service interface for sub-second event
latency; the collector remains the fallback. Until that lands, the daemon
polls SQLite, deliberately: it needs no extra daemon and no auth
tokens.

## Explicit non-goals

Hermes Deck does not: install or update Hermes; read auth.json, .env, or
any credential store; upload data anywhere; spawn privileged helpers; modify
package-managed Omarchy files; or manage Hermes sessions beyond opening
them in the TUI. Remote-machine support rides on Hermes' own mechanisms
(SSH, hermes serve), never on a plugin-bundled bridge daemon.
