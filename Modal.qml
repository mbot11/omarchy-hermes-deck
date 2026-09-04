import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland

// Drop-down Hermes TUI modal, adapted from the MIT-licensed scratch-terminal
// pattern (bscott's Scratch Terminal; ElChacoVeloz/omarchy-hermes-modal).
//
// Each terminal is a real window from Omarchy's default terminal
// (xdg-terminal-exec), parked on a Hyprland special workspace so it keeps
// running while hidden. A Lua window rule registered via `hyprctl eval`
// floats, sizes, centers, and groups the windows keyed on a dedicated
// window class; Hyprland's native groupbar renders the tabs. Toggling is a
// special-workspace dispatch. Terminals are adopted on shell restart so the
// TUI session survives omarchy-shell reloads.
Item {
  id: root

  // Window class stamped on the spawned terminals and the special workspace
  // they live on; the class doubles as the window-rule match.
  readonly property string appClass: "io.github.mbot11.hermes-deck.modal"
  readonly property string specialName: "hermes-deck-modal"
  readonly property string command: "hermes --tui"
  readonly property int tabCeiling: 5

  property var tabAddresses: []
  property bool dropdownVisible: false
  property bool spawning: false
  property var specialByMonitor: ({})
  readonly property int tabCount: tabAddresses.length

  function toggle() {
    if (tabCount === 0) {
      if (!spawning)
        spawn()
    } else {
      dispatchToggle()
    }
  }

  function show() {
    if (tabCount === 0) {
      if (!spawning)
        spawn()
    } else if (!dropdownVisible) {
      dispatchToggle()
    }
  }

  function hide() {
    if (tabCount > 0 && dropdownVisible)
      dispatchToggle()
  }

  function status() {
    if (tabCount === 0)
      return "no terminal"
    return (dropdownVisible ? "visible" : "hidden") + ", " + tabCount + " tab" + (tabCount === 1 ? "" : "s")
  }

  function dispatchToggle() {
    Quickshell.execDetached(["/usr/bin/hyprctl", "dispatch", "hl.dsp.workspace.toggle_special(\"" + specialName + "\")"])
  }

  function spawn() {
    spawning = true
    spawnTimeout.restart()
    ruleProcess.command = ["/usr/bin/hyprctl", "eval", ruleLua()]
    ruleProcess.running = true
  }

  // Globals persist across eval calls but are wiped by a Hyprland config
  // reload, so the spec guard both avoids duplicate rules and self-heals.
  // Sizes use monitor_w/monitor_h expressions; the window is pushed below
  // the groupbar (read from the live config) so tabs stay under the bar.
  function ruleLua() {
    var wf = 0.62
    var hf = 0.55
    var xf = (1 - wf) / 2
    var yf = 0.04
    var size = "monitor_w*" + wf.toFixed(4) + " monitor_h*" + hf.toFixed(4)
    var moveBase = "monitor_w*" + xf.toFixed(4) + " monitor_h*" + yf.toFixed(4)
    return 'local gb = 0 ' + 'local ok, enabled = pcall(hl.get_config, "group:groupbar:enabled") ' + 'if ok and enabled then ' + 'gb = (tonumber(hl.get_config("group:groupbar:height")) or 22) ' + '+ (tonumber(hl.get_config("group:groupbar:indicator_height")) or 1) ' + '+ (tonumber(hl.get_config("group:groupbar:indicator_gap")) or 5) ' + 'end ' + 'local move = "' + moveBase + '+" .. gb ' + 'local spec = "' + size + '|' + moveBase + '|grouped|" .. gb ' + 'if __hermes_deck_modal_spec ~= spec then ' + 'if __hermes_deck_modal_rule then __hermes_deck_modal_rule:set_enabled(false) end ' + '__hermes_deck_modal_rule = hl.window_rule({ ' + 'name = "hermes-deck-modal", ' + 'match = { class = "^io\\\\.github\\\\.mbot11\\\\.hermes-deck\\\\.modal$" }, ' + 'float = true, ' + 'size = "' + size + '", ' + 'move = move, ' + 'group = "set", ' + 'workspace = "special:' + specialName + '" }) ' + '__hermes_deck_modal_spec = spec ' + 'end'
  }

  function updateVisible() {
    var map = specialByMonitor
    for (var key in map) {
      if (map[key] === "special:" + specialName) {
        dropdownVisible = true
        return
      }
    }
    dropdownVisible = false
  }

  function addTab(address) {
    var clean = String(address || "").replace(/^0x/, "")
    if (clean === "" || tabAddresses.indexOf(clean) !== -1)
      return
    if (tabAddresses.length >= tabCeiling)
      return
    var tabs = tabAddresses.slice()
    tabs.push(clean)
    tabAddresses = tabs
  }
  function removeTab(address) {
    var index = tabAddresses.indexOf(String(address || "").replace(/^0x/, ""))
    if (index === -1)
      return
    var tabs = tabAddresses.slice()
    tabs.splice(index, 1)
    tabAddresses = tabs
  }

  // The rule must exist before the window maps; a failed eval still spawns
  // — a misplaced terminal beats a button that silently does nothing.
  Process {
    id: ruleProcess
    running: false
    stderr: StdioCollector {
      id: ruleStderr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0)
        console.warn("hermes-deck: window rule failed:", ruleStderr.text)
      Quickshell.execDetached(["/usr/bin/setsid", "/usr/bin/uwsm-app", "--", "/usr/bin/xdg-terminal-exec", "--app-id=" + root.appClass, "-e", "/usr/bin/bash", "-lc", root.command])
    }
  }

  // Adopt terminals that are already running — the shell restarts more
  // often than the scratchpad dies, and the sessions should survive it.
  Process {
    id: adoptProcess
    running: false
    command: ["/usr/bin/bash", "-c", "/usr/bin/hyprctl clients -j | /usr/bin/jq -r '.[] | select(.class == \"" + root.appClass + "\") | .address'; echo ---; " + "/usr/bin/hyprctl monitors -j | /usr/bin/jq -r '[.[] | select(.specialWorkspace.name == \"special:" + root.specialName + "\")] | length'"]
    stdout: StdioCollector {
      id: adoptStdout
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0)
        return
      var lines = adoptStdout.text.split("\n")
      var tabs = []
      var index = 0
      for (; index < lines.length; index++) {
        var line = lines[index].trim()
        if (line === "---")
          break
        if (line === "")
          continue
        tabs.push(line.replace(/^0x/, ""))
      }
      root.tabAddresses = tabs
      root.dropdownVisible = parseInt(lines[index + 1], 10) > 0
    }
  }

  Component.onCompleted: adoptProcess.running = true

  Timer {
    id: spawnTimeout
    interval: 10000
    onTriggered: root.spawning = false
  }

  Connections {
    target: Hyprland

    function onRawEvent(event) {
      var name = event.name
      var data = event.data
      if (name === "openwindow") {
        var parts = data.split(",")
        if (parts.length >= 3 && parts[2] === root.appClass) {
          root.addTab(parts[0])
          root.spawning = false
          spawnTimeout.stop()
        }
      } else if (name === "closewindow") {
        root.removeTab(data)
      } else if (name === "activespecial") {
        var split = data.lastIndexOf(",")
        if (split === -1)
          return
        var map = root.specialByMonitor
        map[data.substring(split + 1)] = data.substring(0, split)
        root.specialByMonitor = map
        root.updateVisible()
      } else if (name === "monitorremoved") {
        var monitors = root.specialByMonitor
        delete monitors[data]
        root.specialByMonitor = monitors
        root.updateVisible()
      }
    }
  }
}
