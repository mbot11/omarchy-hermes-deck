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

## Search returns nothing

Search reads the `messages_fts` full-text index inside `state.db`. Three
distinct causes, told apart by the error line under the results:

| What the panel says | Cause | Fix |
|---|---|---|
| `0 conversations matching "…"` and the term is visibly in a session | The index is empty or stale while the messages table is not | Rebuild it in Hermes, or confirm the term exists: `sqlite3 ~/.hermes/state.db "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'yourterm'"` |
| `search unavailable: OperationalError` | `messages_fts` is absent — an older store, or one where the index was never created | Search needs FTS5; the rest of the panel is unaffected. Nothing to fix in the plugin. |
| Results but no snippet text | Expected on a match with no surrounding context | Cosmetic only |

Check the raw query the plugin runs, which is the same one the panel uses:

```bash
"$PLUGIN_DIR/scripts/deck-collect" --include-search "your term" | jq '.search'
```

The collector quotes every token before `MATCH`, so `AND`, `OR`, `NOT`, a
stray `"` or a leading `*` are searched for as literal text rather than
interpreted. If you want FTS5 operator behaviour, that is deliberately not
reachable from the panel.

If search is slow, note that its cost scales with conversation history, not
session count — which is why it runs only on the slow refresh and only while
a query is active.

## A rename is rejected

`rename-session` refuses an empty title, a title longer than 200 characters,
and a title containing a newline. The panel shows the exit code; run it by
hand to see the reason:

```bash
"$PLUGIN_DIR/scripts/deck-act" rename-session 20260922_115249_5ffe7d "new title"
```

Spaces, quotes, ampersands and shell metacharacters are all fine — the title
travels as a single argv entry, so it is data, never syntax.

## A pin does not appear in Hermes Desktop

The pin writes the `pinned` column in `state.db`, which is the same flag
Hermes Desktop's sidebar reads. Check the store directly:

```bash
sqlite3 ~/.hermes/state.db \
  "SELECT id, pinned, hidden, archived FROM sessions ORDER BY started_at DESC LIMIT 5"
```

Two things to know:

- The panel lists only sessions where `hidden = 0` and `archived = 0`. A pin
  on a hidden session is stored but not listed here.
- Desktop reads the file on its own refresh, so it may lag the bar by a
  moment. `hermes sessions pinned` is the CLI view of the same flag.

Right-clicking a row pins or unpins it. If the star does not change at all,
the collector is reading a stale snapshot — the action triggers a refresh on
completion, so check the error line for a failed `deck-act` exit code.

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

## Default Agent or Desktop App setup

To verify or configure Hermes as your native Omarchy default coding agent:

```bash
omarchy default agent
omarchy default agent hermes
```

To install or verify the packaged Hermes desktop application:

```bash
omarchy install ai hermes
which hermes-desktop
```

## Clean reset

```bash
omarchy plugin remove io.github.mbot11.hermes-deck
rm -rf "${XDG_STATE_HOME:-$HOME/.local/state}/omarchy/hermes-deck"
omarchy restart shell
```

No other cleanup is necessary; the plugin owns no other files.
