# Hermes Deck v0.2.0 Design Specification

**Feature:** Omarchy Native Agent & Gateway Intelligence Upgrade  
**Target Version:** 0.2.0  
**Repository:** `mbot11/omarchy-hermes-deck`  
**Author:** mbot11  
**Status:** Approved for Implementation  

---

## 1. Objective

Upgrade Hermes Deck from an external-tool companion to a fully synchronized native Omarchy Quattro companion, reflecting Omarchy OS's native adoption of Hermes Agent as a default coding agent and packaged desktop application (`hermes-desktop`).

### Scope: Bundled Tiers
- **Tier 1: Core Native Omarchy Integration**
  - Omarchy default coding agent awareness and 1-click toggle.
  - Native Hermes Desktop Electron app detection and launch action.
  - Contextual native install guidance when offline (`omarchy default agent hermes` / `omarchy install ai hermes`).
  - Session launch environment cleanup (`env -u HERMES_SESSION_SOURCE`).
- **Tier 2: Gateway & Platform Intelligence**
  - Connected messaging platforms parsing (`~/.hermes/gateway_state.json` -> Telegram, Slack, WhatsApp).
  - Active background agent task count (`active_agents`).
  - Rich session source badges in the session browser (`[Desktop]`, `[Telegram]`, `[CLI]`, `[Subagent]`).

---

## 2. Architecture & Invariants

```
+-------------------------------------------------------------+
|                      Omarchy OS Host                        |
|  - ~/.config/omarchy/defaults/agent                         |
|  - /usr/bin/hermes-desktop & /usr/bin/omarchy               |
+-------------------------------------------------------------+
                              | (read-only file / binary checks)
                              v
+-------------------------------------------------------------+
|                 scripts/deck-collect (Python)               |
|  Reads:                                                     |
|    - ~/.hermes/state.db (read-only SQLite)                  |
|    - ~/.hermes/gateway_state.json (read-only JSON)          |
|    - ~/.config/omarchy/defaults/agent (read-only text)      |
|    - check /usr/bin/hermes-desktop (stat / which)           |
|  Emits: Snapshot JSON v2 extended                           |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
|                      Service.qml                            |
|  Exposes: reactive state to Quickshell                      |
+-------------------------------------------------------------+
         |                                           |
         v                                           v
+-----------------------+                 +-------------------+
|     BarWidget.qml     |                 |     Panel.qml     |
| - Status glyph pulse  |                 | - Hero badge      |
| - Rich tooltip        |                 | - Actions flow    |
+-----------------------+                 | - Status block    |
                                          | - Session rows    |
                                          +-------------------+
                                                     |
                                                     v (action name)
                                          +-------------------+
                                          | scripts/deck-act  |
                                          | - fixed allowlist |
                                          | - strict regex    |
                                          +-------------------+
```

### Security Invariants (docs/SECURITY.md)
1. **Fast-path Zero Subprocess:** All new checks in `deck-collect` must be pure local file reads or `os.access` checks. No Python CLI processes.
2. **Fixed-Argument Allowlist:** `deck-act` only executes exact predefined commands. No shell interpolation.
3. **No Credential Touches:** `auth.json`, `.env`, and private tokens are never accessed.
4. **Fail-Soft Isolation:** Any unreadable or missing file returns a graceful default (`false`, empty list, or null) without aborting the snapshot.

---

## 3. Detailed Component Specifications

### 3.1 Data Collector: `scripts/deck-collect`
Extend the snapshot dictionary with:
```json
{
  "isDefaultAgent": true,
  "desktopAvailable": true,
  "gateway": {
    "serviceState": "active",
    "enabled": "enabled",
    "connectedPlatforms": ["telegram"],
    "activeAgentsCount": 0
  }
}
```

- **`isDefaultAgent`:**
  - Read `~/.config/omarchy/defaults/agent` using stdlib `Path.read_text().strip()`.
  - True if equal to `"hermes"`, else False.
- **`desktopAvailable`:**
  - Check `shutil.which("hermes-desktop") is not None` or `Path("/usr/bin/hermes-desktop").is_file()`.
- **`gateway.connectedPlatforms` & `gateway.activeAgentsCount`:**
  - Read `~/.hermes/gateway_state.json`.
  - Extract keys of `platforms` where `platform_data.get("state") == "connected"`.
  - Extract `active_agents` as integer.

### 3.2 Action Dispatcher: `scripts/deck-act`
Add new allowlisted commands:
```bash
  launch-desktop)
    exec /usr/bin/uwsm-app -- /usr/bin/hermes-desktop
    ;;
  set-default-agent)
    exec /usr/bin/omarchy default agent hermes
    ;;
```
Update `new-session`:
```bash
  new-session)
    prompt=${1:-}
    if [[ -z $prompt ]]; then
      launch_tui env -u HERMES_SESSION_SOURCE hermes --yolo
    else
      launch_tui env -u HERMES_SESSION_SOURCE hermes --yolo -z "$prompt"
    fi
    ;;
```

### 3.3 State Adapter: `js/DeckState.js` & `Service.qml`
- `DeckState.js`:
  - `emptyState()`: add `isDefaultAgent: false`, `desktopAvailable: false`, `gateway: { ... connectedPlatforms: [], activeAgentsCount: 0 }`.
  - `accept(raw)`: validate and normalize new fields.
  - Helper `formatPlatformBadge(source)`: returns `"[Desktop]"`, `"[Telegram]"`, `"[CLI]"`, `"[Subagent]"`.
- `Service.qml`:
  - Expose `readonly property bool isDefaultAgent: state.isDefaultAgent === true`
  - Expose `readonly property bool desktopAvailable: state.desktopAvailable === true`
  - Expose `readonly property var connectedPlatforms: state.gateway && Array.isArray(state.gateway.connectedPlatforms) ? state.gateway.connectedPlatforms : []`
  - Expose `readonly property int activeAgentsCount: state.gateway ? Number(state.gateway.activeAgentsCount || 0) : 0`

### 3.4 User Interface: `Panel.qml`
1. **Hero & Default Agent Chip:**
   - If `service.isDefaultAgent`, render a subtle green/accent chip: `DEFAULT AGENT`.
   - If installed but `!service.isDefaultAgent`, render a small clickable link or button: `Set as default agent`.
2. **Actions Flow:**
   - Add **"Open desktop"** button if `service.desktopAvailable`.
   - If uninstalled, replace action buttons with:
     `Install via: omarchy default agent hermes`
3. **Status Block:**
   - Gateway row: `active` + `(Telegram connected)` if platforms are present.
   - If `service.activeAgentsCount > 0`, display `Running tasks: N active`.
4. **Session Rows:**
   - Display `[Desktop]`, `[Telegram]`, `[Subagent]`, or `·` (CLI) badges cleanly styled with caption font.

---

## 4. Verification & Testing Plan

1. **Unit Tests:**
   - `tests/test_collect.py`:
     - Test `isDefaultAgent` detection with mock `~/.config/omarchy/defaults/agent`.
     - Test `desktopAvailable` detection with mock binary.
     - Test `gateway_state.json` parsing with connected platforms and active agents.
     - Test fail-soft behavior when files are corrupt or missing.
   - `tests/test_act.py`:
     - Test `launch-desktop` command generation.
     - Test `set-default-agent` command generation.
     - Test rejection of invalid actions and arguments.
2. **Full Suite Execution:**
   - Execute `tests/run.sh` ensuring all 21+ unit tests pass with zero regressions.
3. **Live System Smoke Test:**
   - Run `scripts/deck-collect | jq .` against real `~/.hermes/` and `~/.config/omarchy/defaults/agent`.
   - Verify `BarWidget.qml` and `Panel.qml` render without QML syntax errors.
