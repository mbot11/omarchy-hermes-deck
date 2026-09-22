# Architecture

## Purpose

Hermes Deck is a live companion surface for a user-local Hermes Agent on
Omarchy: one persistent state daemon feeding a bar glyph and a panel with
Status, Actions, Sessions, Search, Usage, Kanban, and Cron sections, plus a
drop-down TUI modal. It complements Hermes; it never replaces it.

## Component map

```text
Omarchy Quattro shell
├── Service.qml  (kind: service, keepLoaded)
│   ├── collector cadence: fast snapshot (5 s), slow refresh (60 s, TTL-gated)
│   ├── action dispatch: one scripts/deck-act invocation at a time
│   ├── searchQuery: non-empty makes the slow refresh pass --include-search
│   ├── action watchdog (45 s) + generation latch so the panel always recovers
│   ├── notification transitions → omarchy-notification-send
│   └── Modal.qml: drop-down TUI on a Hyprland special workspace
└── BarWidget.qml  (kind: bar-widget)
    ├── state-colored glyph + pulse while working
    ├── shell.serviceFor(pluginId) discovery (retry timer)
    └── Panel.qml
        ├── Status, Actions, Sessions, Search, Usage, Kanban, Cron sections
        ├── session rows: click resumes, right-click pins/unpins
        ├── inline rename field (Enter commits, Escape cancels)
        ├── LATEST badge on the newest conversation
        └── two-step confirmation for destructive actions

scripts/deck-collect (python3 stdlib only)
├── fast path, every poll, no Hermes process:
│   ├── state.db read-only (sqlite3 mode=ro): activity, usage, sessions
│   ├── config.yaml top-level `model` micro-parse (scalar / default: / dict-refusal)
│   ├── ESTOP sentinel read ($HERMES_HOME/ESTOP)
│   └── systemctl --user is-active / is-enabled hermes-gateway.service
├── slow path, TTL-cached in $XDG_STATE_HOME/omarchy/hermes-deck/cache.json:
│   ├── hermes --version        (TTL 30 min)
│   ├── hermes auth list        (TTL 10 min)
│   ├── hermes config get model.default   (TTL 10 min, only if YAML parse empty)
│   └── hermes kanban boards list --json  (TTL 60 s, only with --include-kanban)
├── every snapshot carries `cron` (see the note below):
│   └── with --include-inventory: hermes cron list   (TTL 5 min, inventory only)
│       without it: the last known list, read from the on-disk cache — no
│       subprocess, so the no-spawn invariant holds on the fast path
└── opt-in, only with --include-search <query>:
    └── messages_fts MATCH (every token quoted) joined to messages + sessions,
        deduplicated per session, snippet() over column 0, capped at 20

scripts/deck-act: the only programmatic, non-interactive path that
changes Hermes state (the TUI modal launches a full Hermes session,
which is Hermes by definition)
└── fixed-argument execs: pause, resume, gateway-restart, launch-desktop,
    set-default-agent, new-session [prompt], resume-session <id>,
    set-model <model>, pin-session <id>, unpin-session <id>,
    rename-session <id> <title>
```

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
- **Search is opt-in and read-only.** It rides the FTS5 index Hermes already
  maintains, so it adds no index of its own, no daemon, and no state. It is
  behind a flag because it is the only query in the collector whose cost scales
  with the size of the conversation history rather than with the number of
  sessions, which is why the 5 s path must not carry it.
- **Fail-muted everywhere.** Missing database, missing CLI, malformed
  config: the section degrades and `errors[]` notes it. Unknown state is
  displayed as unknown, never invented.
- **One argv entry per value.** `Service.act(name, arg)` accepts an array and
  turns each element into its own argv entry, so a session title the user
  typed — spaces, quotes, newlines and all — never re-enters shell parsing.
  This is why `rename-session` takes two arguments without a delimiter.

## Trust boundaries

| Boundary | Direction | Control |
|---|---|---|
| `state.db` | read | `file:...?mode=ro` with a percent-encoded path; 0.5 s timeout |
| `messages_fts` | read | same connection; every user token quoted before `MATCH` |
| `config.yaml` | read | 256 KiB cap; refuses to guess a dict-format model block |
| `ESTOP` | read | 4 KiB cap; malformed payload still reports engaged |
| `~/.config/omarchy/defaults/agent` | read | equality check only |
| `hermes` CLI | exec | fixed argv, output capped, wall-clock deadline |
| `systemctl --user` | exec | `is-active` / `is-enabled` only, never lifecycle |
| `deck-act` | exec | action-name allowlist; every dynamic value regex-gated |
| Collector cache | write | `mkstemp` + atomic `os.replace`, inside the plugin's own state dir |
| Network | none | the plugin makes no request of its own |

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
package-managed Omarchy files; or manage Hermes sessions beyond opening them,
renaming them, and setting their pin flag. It never archives or deletes a
session. Remote-machine support rides on Hermes' own mechanisms
(SSH, hermes serve), never on a plugin-bundled bridge daemon.

### `cron` is in every snapshot, and the fast path reads it from the cache

The panel polls on a 5 s fast timer and a 60 s slow timer, and inventory is only
refreshed on the slow one. When the fast payload omitted `cron`, the consumer saw
a missing key, reset its list to empty, and the Cron section flickered between
populated and gone roughly eleven times per refresh. The collector therefore
emits `cron` unconditionally: the inventory path fills it from `hermes cron
list`, and the fast path fills it from the on-disk cache, which is a file read
and spawns nothing. `DeckState.accept` additionally carries the previous list
forward when a payload omits the key entirely, so an older collector cannot
erase it either. An explicitly empty list is honoured as the real fact it is.
