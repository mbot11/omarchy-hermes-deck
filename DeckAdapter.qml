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
  // `search` is the user's FTS5 query; empty means the search section is not
  // requested at all, so the fast snapshot stays free of it.
  function collectArgs(dir, slow, kanban, search, inventory) {
    var argv = ["/usr/bin/python3", dir + "/scripts/deck-collect"]
    if (slow) argv.push("--include-slow")
    if (slow && kanban) argv.push("--include-kanban")
    // Inventory rides the slow refresh only: it costs a `hermes cron list`
    // call, and the fast snapshot must stay subprocess-free.
    if (slow && inventory) argv.push("--include-inventory")
    if (search !== undefined && search !== null && String(search).trim() !== "")
      argv.push("--include-search", String(search).trim())
    return argv
  }

  // `arg` may be a single value or an array. An array becomes one argv entry
  // per element, so a session title containing spaces, quotes or newlines is
  // still exactly one argument and never re-parsed as shell syntax. deck-act
  // therefore never has to un-encode anything.
  function actArgs(dir, name, arg) {
    var argv = [dir + "/scripts/deck-act", name]
    if (Array.isArray(arg)) {
      for (var i = 0; i < arg.length; i++) {
        var part = arg[i]
        if (part !== undefined && part !== null && String(part) !== "")
          argv.push(String(part))
      }
    } else if (arg !== undefined && arg !== null && String(arg) !== "") {
      argv.push(String(arg))
    }
    return argv
  }
}
