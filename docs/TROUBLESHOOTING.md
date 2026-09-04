# Troubleshooting

## Glyph shows nothing / "waiting for service"

The shell populates services asynchronously after widget construction. If
it stays empty after a few seconds:

```bash
omarchy-shell shell rescanPlugins
omarchy restart shell
omarchy-shell shell ping
```

## Panel shows stale data

State refreshes every 5 s (fast) and 60 s (slow). The slow fields
(version, providers, kanban) are TTL-cached; force a refresh with a
middle-click on the glyph, or run the collector directly:

```bash
PLUGIN_DIR="$HOME/.config/omarchy/plugins/io.github.mbot11.hermes-deck"
"$PLUGIN_DIR/scripts/deck-collect" --include-slow --include-kanban | jq .
```

## Sessions or usage are empty

The collector reads `~/.hermes/state.db` read-only. If Hermes has never
run, or `HERMES_HOME` points elsewhere, those sections stay empty and
`errors[]` explains why:

```bash
.../scripts/deck-collect | jq '.errors'
```

## Model shows "unknown"

Custom-endpoint dict-format `model:` blocks are deliberately not parsed
(they can hide a wrong answer, see hermes-harness#2). The collector falls
back to `hermes config get model.default`, TTL-cached for 10 minutes.

## Actions fail

Actions run `scripts/deck-act`. Test manually:

```bash
"$PLUGIN_DIR/scripts/deck-act" pause && "$PLUGIN_DIR/scripts/deck-act" resume
```

`hermes pause` engages the emergency stop (visible as the amber state and
banner). Failures print the exit code in the panel's error line.

## TUI modal misbehaves

The modal parks `hermes --tui` in the `special:hermes-deck-modal`
workspace under window class `io.github.mbot11.hermes-deck.modal`.

```bash
omarchy-shell io.github.mbot11.hermes-deck.modal status
hyprctl clients -j | jq '.[] | select(.class | contains("hermes-deck"))'
```

After a Hyprland config reload the window rule self-heals on next spawn.
Kill stray terminals: close the tab (middle-click quit via IPC `hide`,
then `hyprctl clients` cleanup if needed).

## Clean reset

```bash
omarchy plugin remove io.github.mbot11.hermes-deck
rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/hermes-deck"
omarchy restart shell
```

No other cleanup is necessary; the plugin owns no other files.
