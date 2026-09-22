#!/usr/bin/env node
// Behavioural tests for js/DeckState.js.
//
// DeckState is a .pragma library of pure helpers, so it is testable without a
// QML engine: load the file into a vm context and call the functions directly.
// This is the only automated coverage the JS layer has — without it a
// mis-shaped snapshot reaches the panel and silently renders blank.

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SOURCE = path.join(__dirname, "..", "js", "DeckState.js");

function load() {
  // `.pragma library` is QML-only syntax that a JS engine cannot parse, so it
  // is stripped before evaluation. Nothing else about the file is altered:
  // the helpers under test run exactly as the panel runs them.
  const code = fs.readFileSync(SOURCE, "utf8").replace(/^\s*\.pragma\s+library\s*$/m, "");
  // The context's own globalThis must not be shadowed, or the appended export
  // line writes to a null.
  const context = {};
  vm.createContext(context);
  vm.runInContext(
    code + "\n;globalThis.__deck = { emptyState, accept, cronSummary, deckState };",
    context);
  return context.__deck;
}

const deck = load();
let failures = 0;

function check(label, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) {
    console.log(`  ok       ${label}`);
  } else {
    failures++;
    console.log(`  FAIL     ${label}\n             expected ${JSON.stringify(expected)}` +
      `\n             actual   ${JSON.stringify(actual)}`);
  }
}

// ── emptyState: the shape the panel reads before the first snapshot ─────────
console.log("emptyState");
const empty = deck.emptyState();
check("declares cron as an empty list", Array.isArray(empty.cron) && empty.cron.length === 0,
  true);

// ── accept: a snapshot carrying inventory ──────────────────────────────────
console.log("accept");
const snapshot = JSON.stringify({
  schemaVersion: 2,
  id: "hermes-deck",
  cron: [
    { name: "nightly-backup", schedule: "0 3 * * *", paused: false, running: false },
    { name: "weekly-digest", schedule: "0 9 * * 1", paused: true, running: false },
  ],
});
const state = deck.accept(snapshot);
check("carries cron through", state.cron.length, 2);

const legacy = deck.accept(JSON.stringify({ schemaVersion: 2, id: "hermes-deck" }));
check("defaults cron when the collector omits it", legacy.cron, []);

const hostile = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck", cron: "not a list",
}));
check("refuses a non-list cron", hostile.cron, []);

const junk = deck.accept(JSON.stringify({
  schemaVersion: 2,
  id: "hermes-deck",
  cron: [
    { name: "good", schedule: "* * * * *", paused: true },
    null,
    { schedule: "* * * * *" },
  ],
}));
check("drops malformed entries rather than the whole list", junk.cron.length, 1);

// ── the flicker: a fast tick must not erase the inventory ──────────────────
// Inventory is fetched on the 60 s slow tick but the fast tick runs every 5 s.
// When `accept` reset `cron` to [] on a payload without the key, the section was
// destroyed ~11 times more often than it was rebuilt, so it visibly flickered.
console.log("fast tick does not erase the inventory");
const withJobs = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck",
  cron: [{ name: "nightly", schedule: "0 3 * * *", paused: false },
         { name: "weekly", schedule: "0 9 * * 1", paused: true }],
}));
check("slow tick populated the list", withJobs.cron.length, 2);

// The collector's fast path now carries the cached list, so this is the real
// sequence: a populated fast payload keeps the list populated.
const afterFast = deck.accept(
  JSON.stringify({
    schemaVersion: 2, id: "hermes-deck",
    cron: [{ name: "nightly", schedule: "0 3 * * *", paused: false },
           { name: "weekly", schedule: "0 9 * * 1", paused: true }],
  }),
  withJobs.cron,
);
check("fast tick retained the list", afterFast.cron.length, 2);
check("fast tick retained the paused count",
  deck.cronSummary(afterFast.cron), { total: 2, paused: 1 });

// Belt: a payload that omits the key entirely (older collector, truncated
// payload) must not erase what we already know.
const afterAbsent = deck.accept(
  JSON.stringify({ schemaVersion: 2, id: "hermes-deck" }),
  withJobs.cron,
);
check("an absent key retains the previous list", afterAbsent.cron.length, 2);

const afterEmpty = deck.accept(
  JSON.stringify({ schemaVersion: 2, id: "hermes-deck", cron: [] }),
  withJobs.cron,
);
check("an explicit empty list is honoured, not retained", afterEmpty.cron.length, 0);

// ── the emergency stop must survive accept() ───────────────────────────────
// `accept` copies only keys present in `emptyState()`. `paused` was absent from
// that template, so it was dropped from every snapshot and the ESTOP banner, the
// pause/resume button label and the paused notifications were all dead code.
console.log("emergency stop survives accept");
const estop = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck", installed: true,
  paused: true, reason: "deploy window",
  gateway: { serviceState: "active", enabled: "enabled", connectedPlatforms: [], activeAgentsCount: 0 },
}));
check("paused is carried", estop.paused, true);
check("deckState reports paused", deck.deckState(estop), "paused");
check("reason is carried", estop.reason, "deploy window");

const notPaused = deck.accept(JSON.stringify({ schemaVersion: 2, id: "hermes-deck", installed: true }));
check("absent paused defaults to false", notPaused.paused, false);
check("deckState is not paused", deck.deckState(notPaused), "gatewayDown");

// ── activity.working must survive accept() ─────────────────────────────────
// It was overwritten unconditionally, so deckState() could never return
// "working" and the working indicator plus the finished-notification were dead.
console.log("activity.working survives accept");
const working = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck", installed: true,
  activity: { working: true, lastMessageAt: 1758500000, secondsSinceLastMessage: 3 },
  gateway: { serviceState: "active", enabled: "enabled", connectedPlatforms: [], activeAgentsCount: 0 },
}));
check("working is carried", working.activity.working, true);
check("deckState reports working", deck.deckState(working), "working");
check("lastMessageAt is carried", working.activity.lastMessageAt, 1758500000);

const noActivity = deck.accept(JSON.stringify({ schemaVersion: 2, id: "hermes-deck", installed: true }));
check("absent activity defaults to not-working", noActivity.activity.working, false);
check("absent activity keeps the shape",
  Object.keys(noActivity.activity).sort(), ["lastMessageAt", "secondsSinceLastMessage", "working"]);

// ── cronSummary: what the panel header renders ─────────────────────────────
console.log("cronSummary");
check("counts total and paused",
  deck.cronSummary([
    { name: "a", paused: true }, { name: "b", paused: false },
  ]),
  { total: 2, paused: 1 });

check("empty list", deck.cronSummary([]), { total: 0, paused: 0 });
check("tolerates a non-list", deck.cronSummary(undefined), { total: 0, paused: 0 });
check("tolerates junk entries", deck.cronSummary([null, { paused: true }]),
  { total: 1, paused: 1 });

// ── Cron: the shape the CRON ROWS depend on ───────────────────────────────
// A job row renders `modelData.schedule`. Two real jobs were created on the
// machine to verify this live (one active, one paused) — and the first live run
// showed blank schedules because the row rendered "active"/"paused" instead,
// repeating state the marker, colour and section summary already conveyed. These
// assertions pin the field the row needs, through the real accept() path.
console.log("cron rows keep the schedule the panel renders");
const cronDeck = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck",
  cron: [{ name: "deck-visual-test", schedule: "0 3 * * *", paused: false },
         { name: "deck-paused-test", schedule: "30 4 * * *", paused: true }],
}));
check("both jobs survive accept", cronDeck.cron.length, 2);
check("an active job keeps its schedule",
  cronDeck.cron[0].schedule, "0 3 * * *");
check("a paused job keeps its name",
  cronDeck.cron[1].name, "deck-paused-test");
check("a paused job keeps its schedule",
  cronDeck.cron[1].schedule, "30 4 * * *");
check("the paused flag survives", cronDeck.cron[1].paused, true);
check("the summary counts both and the paused one",
  deck.cronSummary(cronDeck.cron), { total: 2, paused: 1 });

console.log("search results survive a fast tick");
// Only the slow tick passes --include-search. The fast tick (every 5s while the
// panel is open) reports query "" with 0 results, and accept() copied that over
// the slow tick's answer — so the panel showed "0 conversation(s) matching" for
// queries the collector answers with 17. Verified live before and after.
const searched = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck",
  search: { query: "gateway", results: [{ id: "a" }, { id: "b" }], totalHits: 17 },
}), [], { query: "", results: [], totalHits: 0 });
check("the slow tick's results land", searched.search.totalHits, 17);

const afterFastTick = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck",
  search: { query: "", results: [], totalHits: 0 },
}), [], searched.search);
check("a fast tick does not erase them", afterFastTick.search.totalHits, 17);
check("the query survives too", afterFastTick.search.query, "gateway");
check("the result rows survive", afterFastTick.search.results.length, 2);

// A real answer of 0 hits is a FACT and must win over a stale result set.
const zero = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck",
  search: { query: "zzzznothing", results: [], totalHits: 0 },
}), [], searched.search);
check("an explicit 0-hit answer is not replaced by stale hits",
  zero.search.totalHits, 0);
check("and its query is kept", zero.search.query, "zzzznothing");

// Clearing the search must not resurrect old results.
const cleared = deck.accept(JSON.stringify({
  schemaVersion: 2, id: "hermes-deck",
  search: { query: "", results: [], totalHits: 0 },
}), [], { query: "", results: [], totalHits: 0 });
check("an empty previous search stays empty", cleared.search.totalHits, 0);

console.log(`\n${failures === 0 ? "PASS" : "FAILED"} (${failures} failure(s))`);
process.exit(failures === 0 ? 0 : 1);
