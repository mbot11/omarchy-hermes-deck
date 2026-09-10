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
    errors: []
  }
}

// Parse and validate one collector snapshot. Throws on malformed input so
// the caller can keep its previous model (fail-muted).
function accept(raw) {
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
    state.activity = { working: false, lastMessageAt: null, secondsSinceLastMessage: null }
  if (!Array.isArray(state.sessions)) state.sessions = []
  if (!Array.isArray(state.auth)) state.auth = []
  if (!state.kanban || typeof state.kanban !== "object") state.kanban = { available: false, boards: [] }
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

function platformBadge(source) {
  var s = String(source || "").toLowerCase()
  if (s === "desktop") return "Desktop"
  if (s === "telegram") return "Telegram"
  if (s === "subagent") return "Subagent"
  if (s === "kanban") return "Kanban"
  if (s === "cli") return "CLI"
  return s ? s : ""
}
