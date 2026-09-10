# Security

## Trust boundary

Omarchy Quattro plugins run unsandboxed with the current user's permissions.
Hermes Deck therefore narrows itself:

- **Reads only**: `~/.hermes/state.db` (SQLite, `mode=ro`), the top-level
  `model` value of `~/.hermes/config.yaml` (bounded to 256 KiB), the
  `ESTOP` sentinel (bounded to 4 KiB), and read-only
  `systemctl --user is-active/is-enabled hermes-gateway.service` output.
- **Credential stores**: the plugin never opens `auth.json`, `.env`, or any
  credential file itself. One caveat stated plainly: the plugin executes
  `hermes auth list`, which is Hermes reading its own auth state
  internally; the plugin retains only provider names and credential
  counts, and the raw output is discarded in memory.
- **Writes only**: `$XDG_STATE_HOME/omarchy/hermes-deck/cache.json`
  (TTL cache, non-secret fields only) and its own atomic scratch file in
  the same directory. Removing the plugin directory leaves no
  configuration behind.

## State mutation

`scripts/deck-act` is the only *programmatic, non-interactive* path through
which the plugin changes Hermes state (the TUI modal launches a full Hermes
session, which is Hermes by definition, not a plugin action path). The
dispatcher executes a fixed allowlist of documented commands with fixed
arguments; dynamic values are validated before use:

- session ids must match `^[0-9a-zA-Z_-]{8,64}$` (Hermes ids are
  `YYYYMMDD_HHMMSS_xxxxxx`; UUID-style ids still pass);
- model ids must match `^[A-Za-z0-9._:/][A-Za-z0-9._:/-]{0,127}\$`, so the
  first character may not be a dash, so no value can reach the Hermes CLI
  looking like an option flag;
- the optional `-z` prompt is passed as a single argv entry, never through
  a shell on the plugin side. The final custody hop is Omarchy's
  `omarchy-launch-tui` → `xdg-terminal-exec -e`, which forwards argv
  verbatim on the stock Omarchy terminal (foot); the guarantee therefore
  covers the plugin and the Omarchy helper, not arbitrary terminal
  emulators that re-join argv;
- `new-session` launches Hermes in its unattended mode (`hermes --yolo`),
  the same convention Omarchy's packaged agent launchers use; in-TUI
  approvals are auto-accepted by design, and the action is an explicit
  user click;
- destructive actions (`pause`, `gateway-restart`) require a second click
  within 3 seconds in the panel.

Gateway control uses Hermes' own `hermes gateway restart`, not raw
systemctl writes.

Desktop launching uses `/usr/bin/uwsm-app -- /usr/bin/hermes-desktop` with
fixed arguments, and default agent configuration uses
`/usr/bin/omarchy default agent hermes` with fixed arguments.
## Binary resolution

- System tools are pinned to absolute paths: `/usr/bin/python3`,
  `/usr/bin/systemctl`, `/usr/bin/hyprctl`, `/usr/bin/jq`,
  `/usr/bin/bash`, `/usr/bin/setsid`, `/usr/bin/uwsm-app`,
  `/usr/bin/xdg-terminal-exec`, `/usr/bin/omarchy-launch-tui`,
  `/usr/bin/omarchy-notification-send`, `/usr/bin/omarchy`,
  `/usr/bin/hermes-desktop`.
  (typically `~/.local/bin/hermes`) with no fixed system location. The
  test suite stubs it; production resolves it exactly as a user's shell
  would.
- The test suite stubs launchers via `OMARCHY_LAUNCH_TUI`, `OMARCHY_BIN`,
  and `UWSM_APP`; same-user trust boundary either way.

## Display

Every dynamic QML `Text` uses `Text.PlainText`; session titles, board
names, and model ids are displayed as data, never as markup. Notification
headlines and bodies pass through `omarchy-notification-send` as argv
entries, not shell strings.

## Subprocess hygiene

The collector drains child stdout incrementally under a hard 64 KiB cap
with a per-call deadline. Past the cap, the child is killed, so a
pathological stream can neither balloon memory nor outlive its timeout.
No child inherits a TTY. The collector spawns a Hermes CLI process only
for TTL-stale slow fields. Actions receive a 45 s watchdog in the
service: the wedged child is terminated and its late exit is ignored by
generation counter, so panel buttons always recover.

## Known limits

- `hermes auth list` output is parsed as text; a future machine-readable
  interface would be preferred. Only provider names and credential counts
  are retained.
- The TUI modal registers a Hyprland window rule through `hyprctl eval`
  with a plugin-namespaced Lua global. It does not modify configuration
  files on disk.
- The notification daemon receives ESTOP `reason` text and model ids as
  argv bodies. If the daemon renders markup in notification bodies, a
  locally written ESTOP file could inject it, a same-user vector only;
  the plugin passes bodies as plain argv strings.

Report anything that looks like a trust-boundary violation as a GitHub
issue rather than exploiting it publicly.
