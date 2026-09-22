# Hermes Deck

[![CI](https://github.com/mbot11/omarchy-hermes-deck/actions/workflows/ci.yml/badge.svg)](https://github.com/mbot11/omarchy-hermes-deck/actions/workflows/ci.yml)
A desktop surface for [Hermes Agent](https://hermes-agent.nousresearch.com) on [Omarchy](https://omarchy.org): a bar glyph that reflects agent state, a panel with status, sessions, usage, and kanban, controls for the emergency stop and gateway, and a drop-down TUI modal. One plugin.
A persistent service-kind daemon polls the Hermes state database read-only; everything else is QML, bash, and argument validation. Tests, docs, and a threat model ship in the repo.

```bash
omarchy plugin add https://github.com/mbot11/omarchy-hermes-deck.git --enable
```

## What it does

![Hermes Deck panel](preview.png)

| Area | Behavior |
|---|---|
| Bar glyph | State-colored: cyan pulse while the agent works, amber while paused, red when the gateway is down. Right-click toggles the TUI modal. |
| Status | Gateway state with connected messaging platforms (Telegram, Slack, etc.) and active background task count, active model, configured auth providers, and Omarchy default agent badge. |
| Actions | Pause/resume the agent (`hermes pause`/`resume` emergency stop), restart the gateway (`hermes gateway restart`), start a new chat, open the native desktop app (`hermes-desktop`), or set as Omarchy default coding agent; destructive actions need a confirming second click. |
| Sessions | Recent conversations with title, age, workspace, model, and origin badges (`[Desktop]`, `[Telegram]`, `[CLI]`); clicking one resumes it in the TUI. Right-click a row to pin or unpin it. A `LATEST` badge marks the newest conversation, which is a different fact from the live working dot. |
| Search | Full-text search across **every message in every session**, powered by the SQLite FTS5 index Hermes already maintains. Type a query, press Enter, and matching conversations appear with a snippet; click one to resume it. |
| Pin / rename | Right-click a session to pin it, or use the inline rename field. **Pinning writes the same flag Hermes Desktop's own sidebar reads**, so a pin set here appears there without a second store. |
| Usage | Today and 7-day token totals with Hermes' own cost estimates, broken down by model. |
| Kanban | Read-only board summaries from `hermes kanban boards list --json`. |
| Cron | Scheduled jobs with their schedule and a paused marker, from `hermes cron list --all`. `--all` is required: a paused job is stored as disabled and is hidden from the default listing. Inventory only — pausing and resuming stay in the CLI. |
| TUI modal | Quake-style drop-down terminal running `hermes --tui` on a Hyprland special workspace; survives shell restarts. |

Desktop notifications fire when the agent finishes long work, when the emergency stop engages or lifts, and when the gateway service changes state. Disable with the **notify** widget setting.

## Requirements

- Omarchy (Quattro shell); the plugin uses the `service` + `bar-widget` kinds
- [Hermes Agent](https://hermes-agent.nousresearch.com) installed (`omarchy default agent hermes` or `omarchy install ai hermes`); every surface degrades gracefully when Hermes is absent
- Python 3 (stdlib only, no pip packages)

## Install

```bash
omarchy plugin add https://github.com/mbot11/omarchy-hermes-deck.git --enable
omarchy plugin enable io.github.mbot11.hermes-deck
```

The glyph lands on the right bar section. Optional hotkeys in `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + SHIFT + H", "Hermes Deck panel", "omarchy-shell io.github.mbot11.hermes-deck toggle")
o.bind("SUPER + ALT + H", "Hermes TUI modal", "omarchy-shell io.github.mbot11.hermes-deck.modal toggle")
```

## Settings

Open the bar settings for the widget:

| Key | Default | Meaning |
|---|---|---|
| `showKanban` | true | Show the Kanban section |
| `showCron` | true | Show the Cron section |
| `notify` | true | Desktop notifications for agent events |

## Panel keys

| Key | Action |
|---|---|
| `Enter` in the search field | Run the search |
| `Escape` in the search field | Clear the search |
| `Enter` in the rename field | Commit the new title |
| `Escape` in the rename field | Cancel the rename |
| Right-click a session row | Pin / unpin that session |
| Left-click a session row | Resume it in the TUI |

## Privacy

The plugin reads Hermes' state database read-only and makes no network requests itself. It never opens `auth.json`, `.env`, or credential stores; the one nuance is that it executes `hermes auth list`, which is Hermes reading its own auth state internally, and the plugin keeps only provider names and counts. Hermes itself (TUI, gateway, messaging platforms) communicates externally by design. See [SECURITY](docs/SECURITY.md).

## Troubleshooting

See [TROUBLESHOOTING](docs/TROUBLESHOOTING.md). Quick checks:

```bash
~/.config/omarchy/plugins/io.github.mbot11.hermes-deck/scripts/deck-collect | jq .
omarchy-shell shell rescanPlugins
omarchy restart shell
```

## Removal

```bash
omarchy plugin disable io.github.mbot11.hermes-deck
omarchy plugin remove io.github.mbot11.hermes-deck --yes
```

The plugin keeps no state outside its own directory. Two optional leftovers are
deliberately not removed by the command above, since neither is the plugin's to
delete:

| Leftover | Path | Why it stays |
|---|---|---|
| Collector cache | `${XDG_STATE_HOME:-~/.local/state}/omarchy/hermes-deck/cache.json` | Holds only TTL-cached `hermes --version`, auth-provider names, kanban board summaries, and cron job names/schedules. No secrets. Delete the directory if you want it gone. |
| Hotkeys | `~/.config/hypr/bindings.lua` | The two `o.bind` lines from [Install](#install) are yours to keep or delete; the plugin never edits this file. Run `hyprctl reload` and check `hyprctl configerrors` after editing. |

If you set Hermes as Omarchy's default coding agent through the panel, that
choice is stored by Omarchy at `~/.config/omarchy/defaults/agent` and is not
removed by uninstalling this plugin. Reset it with `omarchy default agent` and
pick a different agent, or delete the file.

Removing the plugin does not touch Hermes itself, its database, or its
configuration.

## Attribution

- Drop-down TUI modal pattern: bscott's MIT-licensed Scratch Terminal, via ElChacoVeloz/omarchy-hermes-modal.
- Service-discovery and pulse-glyph patterns: Snitch (MIT) and the Omarchy first-party widgets.

## License

MIT, see [LICENSE](LICENSE).
