import QtQuick
import Quickshell
import Quickshell.Io
import "js/DeckState.js" as DeckState

// Persistent Hermes Deck state daemon.
//
// Owns the collector cadence (fast snapshot every 5s, TTL-gated slow
// refresh every 60s), the action queue (one deck-act at a time), the
// desktop-notification transitions, and the drop-down TUI modal.
// BarWidget/Panel consume this instance through shell.serviceFor().
Item {
  id: root

  // Injected by the shell's plugin loader.
  property var shell: null
  property var manifest: null
  property var pluginRegistry: null

  property string pluginDir: ""
  property bool notificationsEnabled: true
  property bool panelOpen: false // set by the panel; drives cadence + kanban
  property bool kanbanEnabled: true

  readonly property string pluginId: "io.github.mbot11.hermes-deck"
  readonly property string modalIpcTarget: pluginId + ".modal"

  property var state: DeckState.emptyState()
  property int revision: 0
  property string lastError: ""
  property bool busy: false // a deck-act invocation is in flight
  readonly property bool installed: state.installed === true
  readonly property bool isDefaultAgent: state.isDefaultAgent === true
  readonly property bool desktopAvailable: state.desktopAvailable === true
  readonly property var connectedPlatforms: state.gateway && Array.isArray(state.gateway.connectedPlatforms) ? state.gateway.connectedPlatforms : []
  readonly property int activeAgentsCount: state.gateway ? Number(state.gateway.activeAgentsCount || 0) : 0
  readonly property bool working: state.activity !== null && state.activity.working === true
  readonly property bool paused: state.paused === true
  readonly property string gatewayState: state.gateway ? String(state.gateway.serviceState || "unknown") : "unknown"
  readonly property string model: String(state.model || "")
  readonly property string version: String(state.version || "")
  readonly property var sessions: Array.isArray(state.sessions) ? state.sessions : []
  readonly property var usage: state.usage && typeof state.usage === "object" ? state.usage : {}
  readonly property var kanban: state.kanban && typeof state.kanban === "object" ? state.kanban : {
    "available": false,
    "boards": []
  }

  // Notification transition trackers.
  property bool wasWorking: false
  property real workingSince: 0
  property bool wasPaused: false
  property string wasGatewayState: ""

  signal actionDone(string action, int exitCode)

  DeckAdapter {
    id: adapter
  }

  Component.onCompleted: {
    if (!root.pluginDir)
      root.pluginDir = adapter.pluginDir(root.manifest)
    Qt.callLater(root.refreshFast)
  }

  function refreshFast() {
    if (collectProc.running)
      return
    collectProc.command = adapter.collectArgs(root.pluginDir, false, false)
    collectProc.running = true
  }

  function refreshSlow() {
    if (collectProc.running)
      return
    collectProc.command = adapter.collectArgs(root.pluginDir, true, root.kanbanEnabled && root.panelOpen)
    collectProc.running = true
  }

  function acceptCollect(text) {
    try {
      var next = DeckState.accept(text)
      handleTransitions(next)
      root.state = next
      root.revision++
      root.lastError = ""
    } catch (e) {
      root.lastError = "Status refresh failed"
    }
  }

  function handleTransitions(next) {
    var now = Date.now()
    var nowWorking = next.activity !== null && next.activity.working === true
    if (nowWorking && !root.wasWorking)
      root.workingSince = now
    if (root.wasWorking && !nowWorking && now - root.workingSince > 45000 && next.installed === true)
      root.notify("Hermes finished", DeckState.shortenModel(next.model), "normal")
    root.wasWorking = nowWorking

    var nowPaused = next.paused === true
    if (nowPaused && !root.wasPaused)
      root.notify("Hermes paused", String(next.reason || "Emergency stop engaged"), "critical")
    else if (!nowPaused && root.wasPaused)
      root.notify("Hermes resumed", "Emergency stop lifted", "normal")
    root.wasPaused = nowPaused

    var gw = next.gateway ? String(next.gateway.serviceState || "") : ""
    if (root.wasGatewayState === "active" && gw !== "" && gw !== "active")
      root.notify("Hermes gateway " + gw, "hermes-gateway.service changed state", "critical")
    root.wasGatewayState = gw
  }

  function notify(headline, body, urgency) {
    if (!root.notificationsEnabled)
      return
    try {
      var argv = ["/usr/bin/omarchy-notification-send", "-u", urgency || "normal", headline]
      if (body)
        argv.push(body)
      Quickshell.execDetached(argv)
    } catch (e) {
      // Notification delivery is best-effort by design.
    }
  }

  // Queue-free action dispatch: one deck-act at a time; the panel disables
  // its buttons while busy.
  function act(name, arg) {
    if (root.busy)
      return false
    actionProc.command = adapter.actArgs(root.pluginDir, name, arg)
    root.busy = true
    actionProc.running = true
    return true
  }

  Process {
    id: collectProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.acceptCollect(text)
    }
    onExited: function(exitCode) {
      if (exitCode !== 0)
        root.lastError = "Collector exited " + exitCode
    }
  }

  Process {
    id: actionProc
    stdout: StdioCollector {
      waitForEnd: true
    }
    stderr: StdioCollector {
      waitForEnd: true
    }
    onExited: function(exitCode) {
      // A watchdog-timed-out run stays latched: the child was written off,
      // so its late exit must not report a result or refresh state. busy
      // clears here, and no new action can dispatch before this runs —
      // act() refuses while busy, so the Process element is never reused
      // while a timed-out child could still be dying.
      if (root.actionTimedOut) {
        root.actionTimedOut = false
        root.busy = false
        return
      }
      // argv is [deck-act, action, arg?]; slot 1 is the action name.
      var name = actionProc.command && actionProc.command.length > 1 ? String(actionProc.command[1]) : ""
      root.busy = false
      root.actionDone(name, exitCode)
      // pause/resume/gateway/model actions all change collected state.
      Qt.callLater(root.refreshSlow)
    }
  }
  // Watchdog: a wedged deck-act child must not disable panel actions
  // forever. Hermes CLI calls are given a generous ceiling; on expiry the
  // action is written off and the surface recovers.
  Timer {
    id: actionWatchdog
    interval: 45000
    repeat: false
    onTriggered: {
      if (root.busy) {
        // Terminate the wedged child and latch: busy stays true until the
        // process actually exits (its onExited clears the latch), so the
        // Process element is never reused while the old child may still
        // be dying.
        root.actionTimedOut = true
        actionProc.running = false
        root.lastError = "Action timed out"
      }
    }
  }
  onBusyChanged: {
    if (root.busy)
      actionWatchdog.restart()
    else
      actionWatchdog.stop()
  }

  // Adaptive cadence: 5 s while the panel is open or the agent is working
  // (live glyph + snappy finish notification), 15 s when idle and closed —
  // the idle glyph does not need sub-minute freshness, and each collect is
  // a fresh Python process worth budgeting.
  readonly property int fastIntervalMs: (panelOpen || working) ? 5000 : 15000

  Timer {
    interval: root.fastIntervalMs
    running: true
    repeat: true
    triggeredOnStart: false
    onTriggered: root.refreshFast()
  }

  // Slow refresh; the collector's TTLs decide whether any hermes CLI call
  // actually happens, so this is cheap even when the panel is closed. The
  // kanban CLI call is the heaviest recurring cost, so it is only pulled
  // while the panel is open.
  Timer {
    interval: 60000
    running: true
    repeat: true
    onTriggered: root.refreshSlow()
  }

  Modal {
    id: modal
  }

  function toggleModal() {
    modal.toggle()
  }

  IpcHandler {
    target: root.modalIpcTarget

    function toggle(): void {
      root.toggleModal()
    }

    function show(): void {
      modal.show()
    }

    function hide(): void {
      modal.hide()
    }

    function status(): string {
      return modal.status()
    }
  }
}
