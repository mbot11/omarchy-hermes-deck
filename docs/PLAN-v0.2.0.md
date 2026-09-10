# Hermes Deck v0.2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Hermes Deck to v0.2.0 with native Omarchy OS integration (default agent awareness and toggle, native desktop app launching, offline install guidance) and gateway intelligence (connected platforms, active task counts, and session platform badges).

**Architecture:** Pure local, fail-soft reads in `scripts/deck-collect` feed an extended v2 snapshot into `Service.qml`. `Panel.qml` surfaces native status badges, dynamic desktop launch buttons, and session origin tags, while `scripts/deck-act` executes strictly allowlisted fixed-argument actions.

**Tech Stack:** Python 3 (stdlib only, unittest), Bash (POSIX/Bash 5), QML / JavaScript (Quickshell, QtQuick).

**Spec:** `docs/DESIGN-v0.2.0.md`

## Global Constraints

- **Python stdlib only:** No pip packages, external modules, or virtualenv requirements for collector or test scripts.
- **Fast-path zero subprocess:** `deck-collect` without `--include-slow` must never spawn Python CLI processes.
- **Fixed-argument security allowlist:** `deck-act` accepts action identifiers only; no shell interpolation or command strings.
- **No credential access:** `auth.json`, `.env`, tokens, or private keys must never be read, copied, or printed.
- **Backward compatibility:** All existing fields in `manifest.json`, `Service.qml`, and the collector JSON snapshot must remain functional.

---

### Task 1: Data Collection & Snapshot Extensions

**Files:**
- Modify: `scripts/deck-collect:45-70` (paths), `scripts/deck-collect:240-275` (gateway & host inspection)
- Test: `tests/test_collect.py`

**Interfaces:**
- Produces snapshot fields:
  - `isDefaultAgent: bool`
  - `desktopAvailable: bool`
  - `gateway.connectedPlatforms: list[str]`
  - `gateway.activeAgentsCount: int`

- [ ] **Step 1: Write failing tests in `tests/test_collect.py`**
Add tests asserting `isDefaultAgent`, `desktopAvailable`, `gateway.connectedPlatforms`, and `gateway.activeAgentsCount` under mocked filesystem environments.

```python
    def test_default_agent_detected(self) -> None:
        """isDefaultAgent reports True when ~/.config/omarchy/defaults/agent == hermes."""
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "omarchy" / "defaults"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "agent").write_text("hermes\n")
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": td}):
                snap = self.run_collector()
                self.assertTrue(snap.get("isDefaultAgent"))

    def test_gateway_platforms_and_active_agents(self) -> None:
        """gateway_state.json exposes connected platforms and active agent count."""
        with tempfile.TemporaryDirectory() as td:
            gw_file = Path(td) / "gateway_state.json"
            gw_file.write_text(json.dumps({
                "gateway_state": "running",
                "active_agents": 2,
                "platforms": {
                    "telegram": {"state": "connected"},
                    "slack": {"state": "disconnected"}
                }
            }))
            with patch.dict(os.environ, {"HERMES_HOME": td}):
                snap = self.run_collector()
                gw = snap.get("gateway", {})
                self.assertEqual(gw.get("activeAgentsCount"), 2)
                self.assertEqual(gw.get("connectedPlatforms"), ["telegram"])
```

- [ ] **Step 2: Run test to verify it fails**
Run: `python3 tests/test_collect.py CollectorTests.test_default_agent_detected`
Expected: FAIL (missing fields in snapshot).

- [ ] **Step 3: Implement data collection in `scripts/deck-collect`**
Implement:
1. `check_default_agent()`: read `~/.config/omarchy/defaults/agent` (or `$XDG_CONFIG_HOME/omarchy/defaults/agent`). Return True if `"hermes"`.
2. `check_desktop_available()`: check `/usr/bin/hermes-desktop` or `shutil.which("hermes-desktop")`.
3. `collect_gateway_state()`: read `~/.hermes/gateway_state.json`. Extract `connectedPlatforms` and `activeAgentsCount`.
Populate these in the returned snapshot dict.

- [ ] **Step 4: Run test to verify it passes**
Run: `python3 tests/test_collect.py`
Expected: PASS (all tests pass).

---

### Task 2: Action Dispatcher Enhancements

**Files:**
- Modify: `scripts/deck-act:21-70`
- Test: `tests/test_act.py`

**Interfaces:**
- Produces actions:
  - `launch-desktop`
  - `set-default-agent`
- Updates action:
  - `new-session` (wraps with `env -u HERMES_SESSION_SOURCE`)

- [ ] **Step 1: Write failing tests in `tests/test_act.py`**
Add tests asserting `launch-desktop` and `set-default-agent` execution and recording:

```python
    def test_launch_desktop_exec(self) -> None:
        """launch-desktop runs uwsm-app with hermes-desktop."""
        proc = self.run_act("launch-desktop")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.recorded_argv()
        self.assertIn("hermes-desktop", argv)

    def test_set_default_agent_exec(self) -> None:
        """set-default-agent runs omarchy default agent hermes."""
        proc = self.run_act("set-default-agent")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = self.recorded_argv()
        self.assertEqual(argv, ["/usr/bin/omarchy", "default", "agent", "hermes"])
```

- [ ] **Step 2: Run test to verify it fails**
Run: `python3 tests/test_act.py ActTests.test_launch_desktop_exec`
Expected: FAIL with exit code 64 (unknown action).

- [ ] **Step 3: Implement new actions in `scripts/deck-act`**
Add cases in `case "$action" in`:
```bash
  launch-desktop)
    exec /usr/bin/uwsm-app -- /usr/bin/hermes-desktop
    ;;
  set-default-agent)
    exec /usr/bin/omarchy default agent hermes
    ;;
```
And update `new-session` to unset `HERMES_SESSION_SOURCE`.

- [ ] **Step 4: Run test to verify it passes**
Run: `python3 tests/test_act.py`
Expected: PASS (all tests pass).

---

### Task 3: State Management & Service Properties

**Files:**
- Modify: `js/DeckState.js`
- Modify: `Service.qml`

**Interfaces:**
- Produces reactive properties in `Service.qml`:
  - `isDefaultAgent: bool`
  - `desktopAvailable: bool`
  - `connectedPlatforms: list`
  - `activeAgentsCount: int`
- Produces helper in `js/DeckState.js`:
  - `platformBadge(source): string`

- [ ] **Step 1: Update `js/DeckState.js`**
Update `emptyState()` and `accept(raw)` to validate and sanitize `isDefaultAgent`, `desktopAvailable`, `gateway.connectedPlatforms`, and `gateway.activeAgentsCount`.
Add helper:
```javascript
export function platformBadge(source) {
  var s = String(source || "").toLowerCase()
  if (s === "desktop") return "Desktop"
  if (s === "telegram") return "Telegram"
  if (s === "subagent") return "Subagent"
  if (s === "kanban") return "Kanban"
  return ""
}
```

- [ ] **Step 2: Expose properties on `Service.qml`**
Add reactive properties:
```qml
  readonly property bool isDefaultAgent: state.isDefaultAgent === true
  readonly property bool desktopAvailable: state.desktopAvailable === true
  readonly property var connectedPlatforms: state.gateway && Array.isArray(state.gateway.connectedPlatforms) ? state.gateway.connectedPlatforms : []
  readonly property int activeAgentsCount: state.gateway ? Number(state.gateway.activeAgentsCount || 0) : 0
```

- [ ] **Step 3: Verify syntax and parsing**
Run: `node -e 'const ds = require("./js/DeckState.js"); console.log(ds.emptyState())'` or run `tests/run.sh`.

---

### Task 4: Panel UI Integration

**Files:**
- Modify: `Panel.qml`

**Interfaces:**
- Consumes `Service.qml` new properties
- Renders:
  - Default Agent status chip / toggle
  - "Open Desktop" action button
  - Gateway platform indicators
  - Rich session platform badges

- [ ] **Step 1: Add Default Agent Badge & Set-Default Action**
In `Panel.qml` hero / header:
When `service.isDefaultAgent`, display `[DEFAULT AGENT]` accent badge.
When `service.installed && !service.isDefaultAgent`, render a subtle button or action to `set-default-agent`.

- [ ] **Step 2: Add "Open desktop" button in Actions Flow**
Under `Flow` in Actions:
```qml
          Button {
            visible: root.service !== null && root.service.desktopAvailable === true
            text: "Open desktop"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            onClicked: {
              root.runAction("launch-desktop")
              root.close()
            }
          }
```

- [ ] **Step 3: Update Gateway status line & active tasks**
In Status `Column`:
Update Gateway row to show:
`value: root.service && root.service.connectedPlatforms.length > 0 ? root.service.gatewayState + " (" + root.service.connectedPlatforms.join(", ") + ")" : root.display(root.service && root.service.gatewayState, "unknown")`
Add Active Tasks row if `service.activeAgentsCount > 0`.

- [ ] **Step 4: Update SessionRow with platform badges**
In `SessionRow`:
Replace `◈` / `·` with `DeckState.platformBadge(sessionRow.session.source)`.

---

### Task 5: Documentation, Threat Model & Version Bump

**Files:**
- Modify: `manifest.json` (version `0.2.0`)
- Modify: `docs/SECURITY.md` (document `launch-desktop` and `set-default-agent` allowlist entries)
- Modify: `README.md` & `docs/TROUBLESHOOTING.md` (document Omarchy native install commands)
- Modify: `skill/SKILL.md` (version `0.2.0` sync)

- [ ] **Step 1: Bump version in `manifest.json` and `skill/SKILL.md`**
Change `"version": "0.1.0"` to `"version": "0.2.0"`.

- [ ] **Step 2: Update `docs/SECURITY.md`**
Record the additions of `launch-desktop` and `set-default-agent` in the fixed-argument allowlist table, confirming zero parameter injection or credential access.

- [ ] **Step 3: Update `README.md` and `docs/TROUBLESHOOTING.md`**
Add native Omarchy install references (`omarchy default agent hermes`, `omarchy install ai hermes`).

---

### Task 6: Full Verification & Live Smoke Test

**Files:**
- Run all test scripts in `tests/`
- Smoke test against live system

- [ ] **Step 1: Run complete test suite**
Run: `tests/run.sh`
Expected: All Python unit tests pass, no syntax errors.

- [ ] **Step 2: Run live collector snapshot**
Run: `python3 scripts/deck-collect | jq .`
Expected: Verify `isDefaultAgent`, `desktopAvailable`, and `gateway` fields accurately reflect the host machine.

- [ ] **Step 3: Git status & commit**
Verify clean diff and commit:
```bash
git add .
git commit -m "feat: Hermes Deck 0.2.0 - native Omarchy agent and gateway intelligence"
```
