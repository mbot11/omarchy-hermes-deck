import QtQuick
import Quickshell

// Isolates shell-injection and path assumptions for Hermes Deck, mirroring
// the proven SnitchAdapter pattern. Service and BarWidget both instantiate
// this.
Item {
  id: adapter

  readonly property string pluginId: "io.github.mbot11.hermes-deck"

  function pluginDir(manifest) {
    if (manifest && manifest.__sourceDir)
      return String(manifest.__sourceDir).replace(/\/$/, "")
    return localPluginDir()
  }

  function localPluginDir() {
    var u = String(Qt.resolvedUrl("."))
    if (u.indexOf("file://") === 0)
      u = u.substring(7)
    return u.replace(/\/$/, "")
  }

  function findService(bar, shell, pluginRegistry) {
    var sh = shell || (bar && bar.shell) || null
    if (sh && typeof sh.serviceFor === "function") {
      var s = sh.serviceFor(pluginId)
      if (s)
        return s
    }
    if (sh && typeof sh.firstPartyServiceFor === "function") {
      var s2 = sh.firstPartyServiceFor(pluginId)
      if (s2)
        return s2
    }
    return null
  }

  // Fixed collector invocation; slow fields are TTL-gated inside the script.
  function collectArgs(dir, slow, kanban) {
    var argv = ["/usr/bin/python3", dir + "/scripts/deck-collect"]
    if (slow) argv.push("--include-slow")
    if (slow && kanban) argv.push("--include-kanban")
    return argv
  }

  function actArgs(dir, name, arg) {
    var argv = [dir + "/scripts/deck-act", name]
    if (arg !== undefined && arg !== null && String(arg) !== "")
      argv.push(String(arg))
    return argv
  }
}
