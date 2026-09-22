// Pure helpers for Hermes Deck — no QML imports, safe from any context.
.pragma library

function emptyState() {
  return {
    schemaVersion: 2,
    id: "hermes-deck",
    installed: false,
    isDefaultAgent: false,
    desktopAvailable: false,
    version: "",
    model: "",
    modelSource: "unknown",
    gateway: { serviceState: "unknown", enabled: "unknown", connectedPlatforms: [], activeAgentsCount: 0 },
    reason: "",
    engagedAt: null,
    activity: { working: false, lastMessageAt: null, secondsSinceLastMessage: null },
    usage: {},
    sessions: [],
    auth: [],
    kanban: { available: false, boards: [] },
    search: { query: "", results: [], totalHits: 0 },
    // `paused` is the emergency stop. It MUST be declared here: `accept` copies
    // only keys already present in this template, so a field missing from it is
    // silently dropped from every snapshot — which is what made the ESTOP banner,
    // the pause/resume button label and the paused notifications all dead code.
    paused: false,
    cron: [],
    errors: []
  }
}

// Parse and validate one collector snapshot. Throws on malformed input so
// the caller can keep its previous model (fail-muted).
function accept(raw, previousCron) {
  var parsed = JSON.parse(String(raw || ""))
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
    throw new Error("snapshot is not an object")
  if (parsed.schemaVersion !== 2)
    throw new Error("unsupported snapshot schemaVersion")
  var state = emptyState()
  for (var key in state)
    if (parsed[key] !== undefined) state[key] = parsed[key]
  if (!state.gateway || typeof state.gateway !== "object") {
    state.gateway = { serviceState: "unknown", enabled: "unknown", connectedPlatforms: [], activeAgentsCount: 0 }
  } else {
    if (!Array.isArray(state.gateway.connectedPlatforms)) state.gateway.connectedPlatforms = []
    state.gateway.activeAgentsCount = Number(state.gateway.activeAgentsCount || 0)
  }
  state.isDefaultAgent = Boolean(state.isDefaultAgent)
  state.desktopAvailable = Boolean(state.desktopAvailable)
  // Only default `activity` when the payload did not carry one. The previous
  // version overwrote it UNCONDITIONALLY, so `working` could never be true:
  // deckState() never returned "working", the bar's working dot never lit, and
  // Service.qml's "finished" notification (which fires on a working->idle
  // transition) was dead code. Same failure class as `paused` above.
  if (!parsed.activity || typeof parsed.activity !== "object") {
    state.activity = { working: false, lastMessageAt: null, secondsSinceLastMessage: null }
  } else {
    state.activity = {
      working: parsed.activity.working === true,
      lastMessageAt: parsed.activity.lastMessageAt !== undefined ? parsed.activity.lastMessageAt : null,
      secondsSinceLastMessage: parsed.activity.secondsSinceLastMessage !== undefined ? parsed.activity.secondsSinceLastMessage : null
    }
  }
  if (!Array.isArray(state.sessions)) state.sessions = []
  if (!Array.isArray(state.auth)) state.auth = []
  if (!state.kanban || typeof state.kanban !== "object") state.kanban = { available: false, boards: [] }
  // Inventory is only refreshed on the slow tick, so an absent or malformed
  // `cron` must carry the caller's last known list forward rather than reset to
  // empty — resetting erased the panel's Cron section on every fast tick. An
  // explicitly empty array IS the real fact "no jobs" and is preserved.
  if (!Array.isArray(parsed.cron)) {
    state.cron = Array.isArray(previousCron) ? previousCron : []
  } else {
    var jobs = []
    for (var j = 0; j < state.cron.length; j++) {
      var job = state.cron[j]
      if (!job || typeof job !== "object") continue
      if (typeof job.name !== "string" || job.name === "") continue
      // No `running`: the collector stopped emitting it because nothing read it,
      // so mapping it here would recreate a dead field one layer up.
      jobs.push({
        name: job.name,
        schedule: typeof job.schedule === "string" ? job.schedule : "",
        paused: job.paused === true
      })
    }
    // An explicitly empty list is only meaningful when the source actually
    // reported one. An absent key falls back above.
    state.cron = jobs
  }
  if (!Array.isArray(state.errors)) state.errors = []
  return state
}

function fmtTokens(n) {
  var value = Number(n) || 0
  if (value >= 1e9) return (value / 1e9).toFixed(2) + "B"
  if (value >= 1e6) return (value / 1e6).toFixed(1) + "M"
  if (value >= 1e3) return (value / 1e3).toFixed(1) + "k"
  return String(value)
}

function fmtCost(usd) {
  var value = Number(usd)
  if (!isFinite(value) || value <= 0) return ""
  if (value < 0.01) return "<$0.01"
  if (value < 1) return "$" + value.toFixed(3)
  return "$" + value.toFixed(2)
}

function relTime(ts, nowMs) {
  if (ts === null || ts === undefined) return ""
  var seconds = Math.max(0, (nowMs - ts * 1000) / 1000)
  if (seconds < 90) return "just now"
  var minutes = seconds / 60
  if (minutes < 60) return Math.floor(minutes) + "m ago"
  var hours = minutes / 60
  if (hours < 36) return Math.floor(hours) + "h ago"
  return Math.floor(hours / 24) + "d ago"
}

// Semantic glyph state: offline > paused > gatewayDown > working > idle.
// The precedence matters: an engaged emergency stop outranks "working",
// because new turns are halted regardless of in-flight work.
function deckState(state) {
  if (!state || state.installed !== true) return "offline"
  if (state.paused === true) return "paused"
  var gw = state.gateway && state.gateway.serviceState ? String(state.gateway.serviceState) : "unknown"
  if (gw === "failed" || gw === "inactive" || gw === "unknown") return "gatewayDown"
  if (state.activity && state.activity.working === true) return "working"
  return "idle"
}

// Label text for the bar: what the glyph shows next to itself, if anything.
function pillFor(state) {
  var kind = deckState(state)
  if (kind === "paused") return "⏸"
  if (kind === "working") return "●"
  return ""
}

function shortenModel(model) {
  var text = String(model || "")
  var slash = text.lastIndexOf("/")
  if (slash !== -1) text = text.substring(slash + 1)
  return text.length > 28 ? text.substring(0, 27) + "…" : text
}

// Newest session id in the list, so the panel can badge it distinctly from
// the "currently working" dot. Sessions arrive newest-first from the
// collector, but the badge is derived from the activity timestamps rather
// than from list order, so a reordering of the query cannot mislabel it.
function newestSessionId(sessions) {
  if (!Array.isArray(sessions) || sessions.length === 0) return ""
  var newestId = ""
  var newestTs = -1
  for (var i = 0; i < sessions.length; i++) {
    var s = sessions[i]
    if (!s) continue
    var ts = s.lastActivityAt !== null && s.lastActivityAt !== undefined ? s.lastActivityAt : s.startedAt
    if (ts === null || ts === undefined) continue
    if (Number(ts) > newestTs) {
      newestTs = Number(ts)
      newestId = String(s.id || "")
    }
  }
  return newestId
}

function platformBadge(source) {
  var s = String(source || "").toLowerCase()
  if (s === "desktop") return "Desktop"
  if (s === "telegram") return "Telegram"
  if (s === "subagent") return "Subagent"
  if (s === "kanban") return "Kanban"
  if (s === "cli") return "CLI"
  return s ? s : ""
}

// What the panel's Cron header renders: how many jobs exist and how many are
// paused. Counting here rather than in QML keeps the panel's bindings trivial
// and makes the arithmetic testable outside a QML engine.
function cronSummary(jobs) {
  if (!Array.isArray(jobs)) return { total: 0, paused: 0 }
  var total = 0
  var paused = 0
  for (var i = 0; i < jobs.length; i++) {
    var job = jobs[i]
    if (!job || typeof job !== "object") continue
    total++
    if (job.paused === true) paused++
  }
  return { total: total, paused: paused }
}
